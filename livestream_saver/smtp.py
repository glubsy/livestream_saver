from os import getenv
# import email.message
from tempfile import SpooledTemporaryFile
from zipfile import ZipFile, ZIP_LZMA
from smtplib import SMTP
from pathlib import Path
from ssl import create_default_context
from threading import Thread
from queue import Queue
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart, MIMEBase
from email import encoders

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class NotificationHandler():
    """Handle email notifications."""
    def __init__(self):
        self.disabled = True
        self.q = Queue(10)
        self.thread = Thread(target=self.worker, daemon=True)

    def setup(self, config, args):
        self.disabled = not config.getboolean(
            "DEFAULT", "email_notifications", vars=args, fallback=True
        )
        logger.info(
            f"Notifications are {'active.' if not self.disabled else 'disabled.'}"
        )
        if self.disabled:
            return

        # Override from env variables (which may be all-uppercase)
        keys = (
            "smtp_server", "smtp_port",
            "smtp_login", "smtp_password",
            "from_email", "to_email"
        )
        env_vars = {}
        for key in keys:
            if (value := getenv(key)) or (value := getenv(key.upper())):
                env_vars[key] = value

        self.smtp_server = config.get(
            "DEFAULT", "smtp_server", vars=env_vars, fallback=None
        )
        self.smtp_port = config.getint(
            "DEFAULT", "smtp_port", vars=env_vars, fallback=None
        )
        self.smtp_login = config.get(
            "DEFAULT", "smtp_login", vars=env_vars, fallback=None
        )
        self.smtp_password = config.get(
            "DEFAULT", "smtp_password", vars=env_vars, fallback=None
        )
        self.sender_email = config.get(
            "DEFAULT", "from_email", vars=env_vars, fallback=None
        )
        self.receiver_email = config.get(
            "DEFAULT", "to_email", vars=env_vars, fallback=None
        )

        if (not self.smtp_server
        or not self.smtp_port
        or not self.receiver_email):
            self.disabled = True
            return

        # Fallback in case it is not set
        if not self.sender_email:
            self.sender_email = self.receiver_email

        self.thread.start()

    def __del__(self):
        # block until all tasks are done
        self.q.join()

    def send_email(self, subject, message_text, attachments=[]):
        """High-level interface to send an email.
        :param str subject: subject
        :param str message_text: body of the message
        :param list attachements: list of pathlib.Path to files to attach
        """
        if not self.disabled:
            checked_attachments = []
            for attachment in attachments:
                if attachment.exists():
                    checked_attachments.append(attachment)
            return self.enqueue_email(
                self.create_email(subject, message_text, checked_attachments)
            )

    def create_email(self, subject, message_text, attachments=[]):
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

    def enqueue_email(self, email):
        self.q.put(email)

    def worker(self):
        while True:
            item = self.q.get()
            self._do_send_email(item)
            self.q.task_done()

    def _do_send_email(self, email):
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
