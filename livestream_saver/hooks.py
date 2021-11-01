import os
from typing import Optional
from subprocess import Popen, PIPE, DEVNULL
from datetime import datetime

from livestream_saver.download import YoutubeLiveStream


class HookCommand():
    def __init__(self, cmd, logged) -> None:
        self.cmd : Optional[list] = cmd
        self.logged = logged
        # self.enabled = True

        # Disown the child process, we won't care about it at all
        if os.name == 'posix':
            self._kwargs = {
                'preexec_fn': os.setsid
            }
        else:
            self._kwargs = {
                'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP
            }


    def spawn_subprocess(self, stream: YoutubeLiveStream):
        if not self.cmd:
            return
        cmd = self.cmd[:]
        for item in cmd:
            if item == r"%VIDEO_URL%":
                cmd[cmd.index(item)] = stream.url
        try:
            if self.logged:
                program_name = cmd[0].split(os.sep)[-1]
                suffix = "_" + datetime.now().strftime(r"%Y%m%d_%H-%M-%S") + ".log"
                logname = stream.output_dir / (program_name + suffix)

                with open(logname, "wb") as outfile:
                    p = Popen(
                        cmd,
                        stdin=PIPE,
                        stdout=outfile,
                        stderr=PIPE,
                        **self._kwargs
                    )
            else:
                p = Popen(
                    cmd,
                    stdin=DEVNULL,
                    stdout=DEVNULL,
                    stderr=DEVNULL,
                    **self._kwargs
                )
            stream.logger.info(f"Spawned: {cmd} with PID={p.pid}")
        except Exception as e:
            stream.logger.warning(f"Error spawning {cmd[0]}: {e}")
            pass
