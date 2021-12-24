from os import getenv
from re import Pattern
# import email.message
from typing import Optional, Dict, List
from tempfile import SpooledTemporaryFile
from zipfile import ZipFile, ZIP_LZMA
from smtplib import SMTP
from pathlib import Path
from ssl import create_default_context
from threading import Thread
from queue import Queue
import logging
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email import encoders
from livestream_saver.util import UA, is_wanted_based_on_metadata
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import HTTPError
from json import dumps, loads
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def interpolated(key, value, args) -> Optional[str]:
    """Return a copy of value, interpolated with values from args."""
    if "URL" in key.upper():
        # These exact values can be replaced with None as long as they
        # stand alone (can't concatenate str + None).
        upper = value.upper()
        if upper == "%VIDEO_URL%":
            return args.get("url", None)
        elif upper ==  "%THUMBNAIL_URL%":
            return args.get("thumbnail", {}).get('thumbnails', [{}])[-1].get('url')

    default = ""
    if "%THUMBNAIL_URL%" in value:
        value = value.replace(
            "%THUMBNAIL_URL%", args.get("thumbnail", {})\
            .get('thumbnails', [{}])[-1]\
            .get('url', default)
        )
    if "%VIDEO_URL%" in value:
        value = value.replace("%VIDEO_URL", args.get("url", default))
    if "%START_TIME%" in value:
        start_timestamp = default
        start_time = args.get("startTime")
        if start_time:
            try:
                start_timestamp = "Scheduled for " + str(
                    datetime.utcfromtimestamp(int(start_time))) + " GMT+0"
            except Exception as e:
                logger.debug(f"Error converting startTime: {e}")
        value = value.replace("%START_TIME%", start_timestamp)
    if "%VIDEO_ID%" in value:
        value = value.replace("%VIDEO_ID%", args.get('videoId', default))
    if "%DESCRIPTION%" in value:
        desc = default
        if desc := args.get("description"):
            # Limit output to 100 characters
            desc = desc[:100]
        value = value.replace("%DESCRIPTION%", desc)
    if "%TITLE%" in value:
        value = value.replace("%TITLE%", args.get("title", default))
    if "%AUTHOR%" in value:
        value = value.replace("%AUTHOR%", args.get("author", default))
    if "%LIVE_STATUS%" in value:
        # If member-only stream, this will be something like
        # This video is available to this channel's members on level: LEVEL!
        # (or any higher level). Join this channel to get access to members-only content and other exclusive perks.
        value = value.replace(
            "%LIVE_STATUS%", args.get("liveStatus", default))
    if "%LIVE_STATUS_SHORT%" in value:
        value = value.replace(
            "%LIVE_STATUS_SHORT%", args.get("shortRemainingTime", default))
    if "%LOCAL_SCHEDULED%" in value:
        value = value.replace(
            "%LOCAL_SCHEDULED%", args.get("localScheduledTime", default))
    if "%ISLIVECONTENT%" in value:
        isLive = "Video"
        if args.get("isLive"):
            isLive = "Live Stream"
        value = value.replace("%ISLIVECONTENT%", isLive)
    if "%ISLIVENOW%" in value:
        isLiveNow = "Waiting"
        if args.get("isLiveNow"):
            isLiveNow = "Broadcasting!"
        value = value.replace("%ISLIVENOW%", isLiveNow)
    if "%ISMEMBERSONLY%" in value:
        value = value.replace("%ISMEMBERSONLY", args.get("members-only", default))
    return value


def pop_invalid_values(json_dict) -> Dict:
    """Replace URL keys with None if the value is N/A."""
    # Discord throws "embeds": ["0"] error if the url does not start
    # with http:// scheme, but can we just replace with None and it just works
    for k, v in json_dict.items():
        if isinstance(v, dict):
            pop_invalid_values(v)
        elif isinstance(v, list):
            for i in v:
                if isinstance(i, dict):
                    pop_invalid_values(i)
        elif isinstance(v, str):
            if v == "N/A" and "URL" in k.upper():
                json_dict[k] = None
    return json_dict


def replace_values(json_dict: dict, args: dict) -> Dict:
    """Replace each leaf string node with interpolated values."""
    for k, v in json_dict.items():
        if isinstance(v, dict):
            replace_values(v, args)
        elif isinstance(v, list):
            for i in v:
                if isinstance(i, dict):
                    replace_values(i, args)
        elif isinstance(v, str):
            json_dict[k] = interpolated(k, v, args)
    return json_dict

def parse_and_replace(json_str: Optional[str], args: Dict) -> bytes:
    """Return a copy of the string with placeholders replaced with
    corresponding variables"""
    if not json_str:
        raise Exception("Payload is empty.")
    json_str = json_str.strip()
    # The string from the config file needed to be enclosed in quotes
    json_str = json_str.strip("\"\'")

    # Parse it as JSON to validate format, otherwise throw.
    json_d = loads(json_str)
    replace_values(json_d, args)
    # logger.debug(f"Loaded dict after replace:\n{json_d}")
    # pop_invalid_values(json_d)
    return dumps(json_d).encode()

class WebHook():
    def __init__(self, url: str, payload: bytes, headers: Dict) -> None:
        self.url = url
        self.payload = payload
        self.headers = headers
        self.headers.update( {'Content-Type': 'application/json'} )

    def call_api(self):
        """Payload should be a json in binary format."""
        logger.debug(
            f"Sending POST to {urlparse(self.url).netloc} "
            f"with payload:\n{loads(self.payload)}")
        req = Request(
            self.url,
            headers=self.headers,
            data=self.payload,
            method="POST"
        )
        try:
            with urlopen(req) as res:
                logger.debug(f"Response status: {res.status}")
        except HTTPError as e:
            logger.warning(f"Error calling webhook: {e}")
            # logger.warning(f"{e.reason}")
            # logger.warning(f"{e.headers}")
            logger.warning(f"{e.fp.read()}")


class WebHookFactory():
    """Instanciated by config, to produce WebHook objects, to be passed
    on the Dispatcher's queue for handling."""
    def __init__(
        self, url: str, payload: str, logged: bool,
        event_name: str,
        allow_regex: Optional[Pattern] = None,
        block_regex: Optional[Pattern] = None
    ) -> None:
        self.logged = logged
        self.enabled = True
        self.allow_regex = allow_regex
        self.block_regex = block_regex
        self.event_name: str = event_name
        self.headers = { 'user-agent': UA }
        # The raw payload string from the config file
        self.payload_template: Optional[str] = payload
        self.url = url

    def get(self, args: Dict):
        """Create a WebHook object, using the configured parameters.
        If a regex from the config file matches, return None."""
        if not is_wanted_based_on_metadata(
            (args.get("title"), args.get("description")),
            self.allow_regex, self.block_regex
        ):
            logger.debug(
                f"Skipping webhook for {self.event_name} due to regex filter.")
            return None

        try:
            payload = parse_and_replace(self.payload_template, args)
        except Exception as e:
            logger.exception(e)
            return None

        return WebHook(
            url=self.url,
            payload=payload,
            headers=self.headers.copy()
        )


class EmailHandler():
    """Handle email notifications."""
    def __init__(self):
        self.disabled = False
        self.smtp_server = None
        self.smtp_port = None
        self.smtp_login = None
        self.smtp_password = None
        self.sender_email = None
        self.receiver_email = None

    def setup(self, config, args):
        self.disabled = not config.getboolean(
            "DEFAULT", "email_notifications", vars=args, fallback=True
        )
        logger.info(
            f"E-mail notifications are {'active.' if not self.disabled else 'disabled.'}"
        )
        if self.disabled:
            return

        # Override from env variables (which may be all-uppercase)
        env_keys = (
            "smtp_server", "smtp_port",
            "smtp_login", "smtp_password",
            "from_email", "to_email"
        )
        env_vars = {}
        for key in env_keys:
            if (value := getenv(key)) or (value := getenv(key.upper())):
                env_vars[key] = value

        self.smtp_server = config.get(
            "email", "smtp_server", vars=env_vars, fallback=None
        )
        self.smtp_port = config.getint(
            "email", "smtp_port", vars=env_vars, fallback=None
        )
        self.smtp_login = config.get(
            "email", "smtp_login", vars=env_vars, fallback=None
        )
        self.smtp_password = config.get(
            "email", "smtp_password", vars=env_vars, fallback=None
        )
        self.sender_email = config.get(
            "email", "from_email", vars=env_vars, fallback=None
        )
        self.receiver_email = config.get(
            "email", "to_email", vars=env_vars, fallback=None
        )

        if (not self.smtp_server
        or not self.smtp_port
        or not self.receiver_email):
            self.disabled = True
            return

        # Fallback in case it is not set
        if not self.sender_email:
            self.sender_email = self.receiver_email

    def create_email(self, subject, message_text, attachments=List[Path]):
        """Create an email object.
        :param str subject: subject
        :param str message_text: body of the message
        :param list attachements: list of pathlib.Path to files to attach
        """
        if not attachments:
            # Send a simple plain text
            message = MIMEText(message_text)
        else:
            message = MIMEMultipart()

        message["Subject"] = subject
        message["From"] = self.sender_email
        message["To"] = self.receiver_email

        if not attachments:
            return message

        message.attach(MIMEText(message_text, "plain"))

        # Write temp zip file to disk if bigger than 5MiB
        with SpooledTemporaryFile(max_size=5 * 1024 * 1024) as tmp:
            with ZipFile(
                file=tmp,
                mode='x',
                compression=ZIP_LZMA
            ) as archive:
                # Add each file to the zip file
                for path in attachments:
                    archive.write(path)
            tmp.seek(0)
            part = MIMEBase("application", "zip")
            part.set_payload(tmp.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename="logs.zip",
            )
            message.attach(part)

        return message

    def _do_send_email(self, email):
        if self.disabled:
            return
        if logger.isEnabledFor(logging.INFO):
            logger.info(f"Sending email: {email}")

        context = create_default_context()
        server = SMTP(self.smtp_server, self.smtp_port)

        try:
            # server.ehlo() # Can be omitted
            server.starttls(context=context) # Secure the connection
            # server.ehlo() # Can be omitted
            if self.smtp_login and self.smtp_password:
                server.login(self.smtp_login, self.smtp_password)
            server.sendmail(
                self.sender_email,
                self.receiver_email,
                email.as_string()
            )
        # TODO handle SSL/TLS errors and retry as plaintext?
        except Exception as e:
            logger.error(f"SMTP error: {e}")
            return
        finally:
            server.quit()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Sent email {email.get('subject')}")


class NotificationDispatcher():
    """Singleton controller that acts as an interface to send various
    notifications as emails and webhooks."""
    def __init__(self) -> None:
        self.q = Queue(10)
        self.thread = Thread(target=self.worker, daemon=True)
        self.email_handler = EmailHandler()
        self.webhooks: Dict[str, WebHookFactory] = {}

    def setup(self, config, args):
        self.email_handler.setup(config, args)
        if self.email_handler.disabled and len(self.webhooks) == 0:
            return
        self.thread.start()

    def __del__(self):
        # FIXME this is useless since this class is meant to be a global singleton
        # block until all tasks are done
        self.q.join()

    def worker(self):
        """Consummer thread."""
        while True:
            item = self.q.get()
            if isinstance(item, EmailHandler):
                self.email_handler._do_send_email(item)
            elif isinstance(item, WebHook):
                item.call_api()
            self.q.task_done()

    def send_email(self, subject, message_text, attachments=[]):
        """High-level interface to send an email. Producer thread.
        :param str subject: subject
        :param str message_text: body of the message
        :param list attachements: list of pathlib.Path to files to attach
        """
        if self.email_handler.disabled:
            return
        checked_attachments = []
        for attachment in attachments:
            if attachment.exists():
                checked_attachments.append(attachment)
        if email := self.email_handler.create_email(
            subject, message_text, checked_attachments
        ):
            self.q.put(email)

    def get_webhook(self, hook_name):
        return self.webhooks.get(hook_name, None)

    def call_webhook(self, hook_name, args):
        if webhookfactory := self.get_webhook(hook_name):
            if webhook := webhookfactory.get(args):
                self.q.put(webhook)
