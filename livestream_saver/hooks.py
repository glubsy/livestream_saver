import os
from typing import Optional, Dict, Any
from subprocess import Popen, DEVNULL
from datetime import datetime

from livestream_saver.download import YoutubeLiveStream


class HookCommand():
    def __init__(self, cmd, logged) -> None:
        self.cmd : Optional[list] = cmd
        self.logged = logged
        # self.enabled = True

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


    def spawn_subprocess(self, stream: YoutubeLiveStream):
        if not self.cmd:
            return
        # Make a copy to avoid using stale data
        cmd = self.cmd[:]
        # Replace placeholders with actual values
        for item in cmd:
            if item == r"%VIDEO_URL%":
                cmd[cmd.index(item)] = stream.url
            if item == r"%COOKIE_PATH%":
                cmd[cmd.index(item)] = stream.session.cookie_path

        try:
            if self.logged:
                program_name = cmd[0].split(os.sep)[-1]
                suffix = "_" + datetime.now().strftime(r"%Y%m%d_%H-%M-%S") + ".log"
                logname = stream.output_dir / (program_name + suffix)
                log_handle = open(logname, 'a')
                self._kwargs["stdout"] = log_handle
                self._kwargs["stderr"] = log_handle

            p = Popen(
                cmd,
                **self._kwargs
            )
            stream.logger.info(f"Spawned: {p.args} with PID={p.pid}")
        except Exception as e:
            stream.logger.warning(f"Error spawning {cmd[0]}: {e}")
            pass
