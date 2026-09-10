# process.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import subprocess
import signal
import time
import os
import threading

from io import BytesIO

import logging

log = logging.getLogger(__name__)


class CmdResult(object):

    """
    Command execution result.

    :param command: the command line itself
    :type command: str
    :param exit_status: exit code of the process
    :type exit_status: int
    :param stdout: content of the process stdout
    :type stdout: str
    :param stderr: content of the process stderr
    :type stderr: str
    :param duration: elapsed wall clock time running the process
    :type duration: float
    :param pid: ID of the process
    :type pid: int
    """

    def __init__(self, command="", stdout="", stderr="",
                 exit_status=None, duration=0, pid=None):
        self.command = command
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.interrupted = False
        self.pid = pid

    def __repr__(self):
        cmd_rep = ("Command: %s\n"
                   "Exit status: %s\n"
                   "Duration: %s\n"
                   "Stdout:\n%s\n"
                   "Stderr:\n%s\n"
                   "PID:\n%s\n" % (self.command, self.exit_status,
                                   self.duration, self.stdout, self.stderr,
                                   self.pid))
        if self.interrupted:
            cmd_rep += "Command interrupted by %s\n" % self.interrupted
        return cmd_rep


class CmdError(Exception):

    def __init__(self, command=None, result=None):
        self.command = command
        self.result = result

    def __str__(self):
        if self.result is not None:
            if self.result.interrupted:
                msg = "Command '%s' interrupted by %s"
                msg %= (self.command, self.result.interrupted)
            elif self.result.exit_status is None:
                msg = "Command '%s' failed and is not responding to signals"
                msg %= self.command
            else:
                msg = "Command '%s' failed (rc=%s)"
                msg %= (self.command, self.result.stderr)
            return msg
        else:
            return "CmdError"


class FDDrainer(object):

    def __init__(self, fd, result, name=None, logger=None, logger_prefix='%s'):
        """
        Reads data from a file descriptor in a thread, storing locally in
        a file-like :attr:`data` object.

        :param fd: a file descriptor that will be read (drained) from
        :type fd: int
        :param result: a :class:`CmdResult` instance associated with the process
                       used to detect if the process is still running and
                       if there's still data to be read.
        :type result: a :class:`CmdResult` instance
        :param name: a descriptive name that will be passed to the Thread name
        :type name: str
        :param logger: the logger that will be used to (interactively) write
                       the content from the file descriptor
        :type logger: :class:`logging.Logger`
        :param logger_prefix: the prefix used when logging the data
        :type logger_prefix: str with one %-style string formatter
        """
        self.fd = fd
        self.name = name
        self.data = BytesIO()
        self._logger = logger
        self._logger_prefix = logger_prefix
        self._result = result
        self._thread = None

    def _drainer(self):
        """
        Read from fd, storing and optionally logging the output
        """
        log_content = ''
        while True:
            tmp = os.read(self.fd, 1024)
            if not tmp:
                break
            self.data.write(tmp)
            log_content += tmp.decode()
            if tmp.decode().endswith('\n'):
                for line in log_content.splitlines():
                    if self._logger is not None and line:
                        self._logger.debug(self._logger_prefix, line.strip())
                log_content = ''

            if log_content:
                for line in log_content.splitlines():
                    if self._logger is not None and line:
                        self._logger.debug(self._logger_prefix, line.strip())
                log_content = ''

    def start(self):
        self._thread_lock =  threading.Lock()
        self._thread = threading.Thread(target=self._drainer, name=self.name)
        self._thread.daemon = True
        self._thread.start()

    def flush(self):
        self._thread.join()
        if self._logger is not None:
            for handler in self._logger.handlers:
                file = getattr(handler, 'file', None)
                if file is not None:
                    os.fsync(file.fileno())
                if hasattr(handler, 'close'):
                    handler.close()

class SubProcess(object):

    """
    Run a subprocess in the background, collecting stdout/stderr streams.
    """

    def __init__(self, cmd, shell=True):
        """
        Creates the subprocess object, stdout/err, reader threads and locks.

        :param cmd: Command line to run.
        :type cmd: str
        :param shell: Whether to run the subprocess in a subshell.
        :type shell: bool
        :raises: ValueError if incorrect values are given to parameters
        """
        self.cmd = cmd
        self.result = CmdResult(self.cmd)
        self.shell = shell
        self.log = log
        self._popen = None

        # Drainers used when reading from the PIPEs and writing to
        # files and logs
        self._stdout_drainer = None
        self._stderr_drainer = None

    def __repr__(self):
        """
        Can get class object with "name" or print "name"
        """
        if self._popen is None:
            rc = '(not started)'
        elif self.result.exit_status is None:
            rc = '(running)'
        else:
            rc = self.result.exit_status
        return '%s(cmd=%r, rc=%r)' % (self.__class__.__name__, self.cmd, rc)

    def __str__(self):
        """
        Can get class object with print "name"
        """
        if self._popen is None:
            rc = '(not started)'
        elif self.result.exit_status is None:
            rc = '(running)'
        else:
            rc = '(finished with exit status=%d)' % self.result.exit_status
        return '%s %s' % (self.cmd, rc)

    def _init_subprocess(self):
        if self._popen is None:
            self.log.info("Running '%s'" % self.cmd)
            try:
                self._popen = subprocess.Popen(self.cmd,
                                               stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE,
                                               shell=self.shell)
            except OSError as details:
                details.strerror += " (%s)" % self.cmd
                raise details

            self.start_time = time.time()

            # prepare fd drainers
            self._stdout_drainer = FDDrainer(
                self._popen.stdout.fileno(),
                self.result,
                name = "%s" % self.cmd,
                logger = self.log,
                logger_prefix = "[stdout] %s")
            self._stdout_drainer.start()

            self._stderr_drainer = FDDrainer(
                self._popen.stderr.fileno(),
                self.result,
                name = "%s" % self.cmd,
                logger = self.log,
                logger_prefix = "[stderr] %s")
            self._stderr_drainer.start()

            def signal_handler(signum, frame):
                self.result.interrupted = "signal/ctrl+c"
                self.wait()
                signal.default_int_handler(signum, frame)
            try:
                signal.signal(signal.SIGINT, signal_handler)
            except ValueError:
                self.log.info("Command %s running on a thread" % self.cmd)

    def _fill_results(self, rc):
        self._init_subprocess()
        self.result.exit_status = rc
        if self.result.duration == 0:
            self.result.duration = time.time() - self.start_time
        self.log.info("Command '%s' finished with %s after %ss" %(self.cmd, rc,
                 self.result.duration))
        self.result.pid = self._popen.pid
        self._fill_streams()

    def _fill_streams(self):
        """
        Close subprocess stdout and stderr, and put values into result obj.
        """
        # Cleaning up threads
        if self._stdout_drainer is not None:
            self._stdout_drainer.flush()
        if self._stderr_drainer is not None:
            self._stderr_drainer.flush()
        # Clean subprocess pipes and populate stdout/err
        self.result.stdout = self.get_stdout()
        self.result.stderr = self.get_stderr()

    def start(self):
        """
        Start running the subprocess.

        This method is particularly useful for background processes, since
        you can start the subprocess and not block your test flow.

        :return: Subprocess PID.
        :rtype: int
        """
        self._init_subprocess()
        return self._popen.pid

    def get_stdout(self):
        """
        Get the full stdout of the subprocess so far.

        :return: Standard output of the process.
        :rtype: str
        """
        self._init_subprocess()
        if self._stdout_drainer is not None:
            return self._stdout_drainer.data.getvalue()
        return self._stdout_drainer.data.getvalue()

    def get_stderr(self):
        """
        Get the full stderr of the subprocess so far.

        :return: Standard error of the process.
        :rtype: str
        """
        self._init_subprocess()
        if self._stderr_drainer is not None:
            return self._stderr_drainer.data.getvalue()
        return self._stderr_drainer.data.getvalue()

    def terminate(self):
        """
        Send a :attr:`signal.SIGTERM` to the process.
        """
        self._init_subprocess()
        self.send_signal(signal.SIGTERM)

    def kill(self):
        """
        Send a :attr:`signal.SIGKILL` to the process.
        """
        self._init_subprocess()
        self.send_signal(signal.SIGKILL)

    def send_signal(self, sig):
        """
        Send the specified signal to the process.

        :param sig: Signal to send.
        """
        self._init_subprocess()
        self._popen.send_signal(sig)

    def poll(self):
        """
        Call the subprocess poll() method, fill results if rc is not None.
        """
        self._init_subprocess()
        rc = self._popen.poll()
        if rc is not None:
            self._fill_results(rc)
        return rc

    def wait(self):
        """
        Call the subprocess wait() method, fill results if rc is not None.
        """
        self._init_subprocess()
        rc = self._popen.wait()
        if rc is not None:
            self._fill_results(rc)
        return rc

    def communicate(self):
        """
        Call the subprocess communicate() method, fill results if rc is not None.
        """
        self._init_subprocess()
        (stdout, stderr) = self._popen.communicate()
        print("subprocess communicate resturn: %s, %s" %(stdout.decode(), stderr))
        rc = self._popen.returncode
        if rc is not None:
            self._fill_results(rc)
        return rc


    def stop(self):
        """
        Stop background subprocess.

        Call this method to terminate the background subprocess and
        wait for it results.
        """
        self._init_subprocess()
        if self.result.exit_status is None:
            self.terminate()
        return self.wait()

    def get_pid(self):
        """
        Reports PID of this process
        """
        self._init_subprocess()
        return self._popen.pid

    def run(self, timeout=None, sig=signal.SIGTERM):
        """
        Start a process and wait for it to end, returning the result attr.

        If the process was already started using .start(), this will simply
        wait for it to end.

        :param timeout: Time (seconds) we'll wait until the process is
                        finished. If it's not, we'll try to terminate it
                        and get a status.
        :type timeout: float
        :param sig: Signal to send to the process in case it did not end after
                    the specified timeout,it is "Termination signal" by default.
        :type sig: int
        :returns: The command result object.
        :rtype: A :class:`CmdResult` instance.
        """

        def timeout_handler():
            """
            Action when meet time out.
            """
            self.send_signal(sig)
            self.result.interrupted = "timeout after %ss" % timeout

        self._init_subprocess()
        if timeout is None:
            self.wait()
#            self.communicate()
        elif timeout > 0.0:
            timer = threading.Timer(timeout, timeout_handler)
            try:
                timer.start()
                self.wait()
#                self.communicate()
            finally:
                timer.cancel()

        if self.result.exit_status is None:
            stop_time = time.time() + 1
            while time.time() < stop_time:
                self.poll()
                if self.result.exit_status is not None:
                    break
            else:
                self.kill()
                self.poll()

        # If all this work fails, we're dealing with a zombie process.
        e_msg = 'Zombie Process %s' % self._popen.pid
        assert self.result.exit_status is not None, e_msg

        return self.result


def run(cmd, timeout=None, ignore_status=False, shell=True):
    """
    Run a subprocess, returning a CmdResult object.

    :param cmd: Command line to run.
    :type cmd: str
    :param timeout: Time limit in seconds before attempting to kill the
                    running process. This function will take a few seconds
                    longer than 'timeout' to complete if it has to kill the
                    process.
    :type timeout: float
    :param ignore_status: Whether to raise an exception when command returns
                          =! 0 (False), or not (True).
    :type ignore_status: bool
    :param shell: Whether to run the command on a subshell
    :type shell: bool
    :param log: A class object for "logger.Logger".
    :type env: class object

    :return: An :class:`CmdResult` object.
    :raise: :class:`CmdError`, if ``ignore_status=False``.
    """
    sp = SubProcess(cmd=cmd, shell=shell)
    cmd_result = sp.run(timeout=timeout)
    fail_condition = cmd_result.exit_status != 0 or cmd_result.interrupted
    if fail_condition and not ignore_status:
        raise CmdError(cmd, sp.result)
    return cmd_result