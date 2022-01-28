import os
import re
import logging
from typing import Optional, Dict, Any, List
from subprocess import Popen, DEVNULL
from datetime import datetime
from livestream_saver.util import is_wanted_based_on_metadata

# logger = logging.getLogger("livestream_saver")

class HookCommand():
    def __init__(
    self, 
    cmd: Optional[List], 
    logged: bool,
    event_name: str,
    allow_regex: Optional[re.Pattern] = None,
    block_regex: Optional[re.Pattern] = None
    ) -> None:
        self.cmd : Optional[List] = cmd
        self.logged = logged
        self.enabled = True
        self.call_only_once = True
        self.allow_regex = allow_regex
        self.block_regex = block_regex
        self.event_name: str = event_name

        self._kwargs: Dict[Any, Any] = {
            "stdin": DEVNULL,
            "stdout": DEVNULL,
            "stderr": DEVNULL,
        }
        # Disown the child process, we won't care about it at all
        # cf. https://pymotw.com/2/subprocess/#process-groups-sessions
        if os.name == 'posix':
            self._kwargs.update(
                {
                    "start_new_session": True,  # replaces 'preexec_fn': os.setsid
                }
            )
        else:
            self._kwargs.update(
                {
                    "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                }
            )

    def spawn_subprocess(self, args: Dict):
        """args are supposed to be prepared in advance by the caller."""
        # The HookCommand is a singleton, be careful when reusing it!
        if not self.enabled or not self.cmd:
            return
        if self.enabled and self.call_only_once:
            self.enabled = False
        
        logger = args.get("logger", logging.getLogger("livestream_saver"))
        
        data = (args.get("title"), args.get("description"))
        if not is_wanted_based_on_metadata(
            data, self.allow_regex, self.block_regex):
            logger.warning(
                f"Skipping command spawning for event {self.event_name} because"
                " one video metadata elements do not satisfy filter regexes: "
                f"allowed regex: {self.allow_regex}, blocking regex: {self.block_regex}."
            )
            return

        def replace_placeholders(cmd: List) -> List:
            new = []
            patched = False
            for item in cmd:
                if item == r"%VIDEO_URL%":
                    if url := args.get('url', None):
                        # cmd[cmd.index(item)] = url
                        new.append(url)
                        continue
                    else:
                        raise Exception(f"No URL found in video {args}. Skipping command {self.cmd}.")
                if item == r"%COOKIE_PATH%":
                    # if parent.session.cookie_path is not None:
                    if cookie_path := args.get("cookie_path", None):
                        # cmd[cmd.index(item)] = cookie_path
                        new.append(cookie_path)
                        continue
                    elif cmd[cmd.index(item) - 1] == "--cookies":
                        logger.warning(
                            "Detected --cookies argument in custom (yt-dlp?) command"
                            " but missing cookie-path option. Removing and continuing...")
                        # Previous entry should be "--cookies" in new list too
                        new.pop()
                        patched = True
                        continue
                    else:
                        raise Exception(f"No cookie path submitted. Skipping command {self.cmd}.")
                new.append(item)
            if patched:
                logger.warning(f"{self.event_name} command after replacement: {new}.")
            return new

        # Make a copy to avoid reusing stale data
        try:
            cmd = replace_placeholders(self.cmd[:])
        except Exception as e:
            logger.warning(e)
            return

        try:
            if self.logged:
                program_name = cmd[0].split(os.sep)[-1]
                suffix = "_" + datetime.now().strftime(r"%Y%m%d_%H-%M-%S") + ".log"
                logname = args.get("output_dir", os.getcwd()) / (program_name + suffix)
                log_handle = open(logname, 'a')
                self._kwargs["stdout"] = log_handle
                self._kwargs["stderr"] = log_handle

            p = Popen(
                cmd,
                **self._kwargs
            )
            logger.info(f"Spawned: {p.args} with PID={p.pid}")
        except Exception as e:
            logger.warning(f"Error spawning {cmd}: {e}")
            pass

