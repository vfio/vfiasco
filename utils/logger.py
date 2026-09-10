# logger.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging
import os
import time

from utils import config
from utils import paths

_cfg = config.config_parser(['vfiasco.ini'])
_LEVELS = {
    'DEBUG':    logging.DEBUG,
    'INFO':     logging.INFO,
    'WARNING':  logging.WARNING,
    'ERROR':    logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
_console_level = _LEVELS[_cfg['logging']['console_level'].upper()]
_file_level    = _LEVELS[_cfg['logging']['file_level'].upper()]

_fmt = logging.Formatter('%(asctime)s %(levelname)-5.5s| %(message)s', '%H:%M:%S')


def _setup_root_logger():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        sh = logging.StreamHandler()
        sh.setFormatter(_fmt)
        sh.setLevel(_console_level)
        root.addHandler(sh)


_setup_root_logger()


class Logger:
    def __init__(self, logger_name, log_file, clevel=logging.INFO, Flevel=logging.DEBUG):
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter('%(asctime)s %(levelname)-5.5s| %(message)s', '%H:%M:%S')
        self.sh = logging.StreamHandler()
        self.sh.setFormatter(fmt)
        self.sh.setLevel(clevel)
        self.fh = logging.FileHandler(log_file)
        self.fh.setFormatter(fmt)
        self.fh.setLevel(Flevel)
        self.logger.propagate = False
        if not self.logger.handlers:
            self.logger.addHandler(self.sh)
        self.logger.addHandler(self.fh)
        # Also attach file handler to root so all loggers (e.g. utils.process)
        # write to test.log — not just this named logger.
        logging.getLogger().addHandler(self.fh)

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def warn(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def critical(self, message):
        self.logger.critical(message)

    def remove_handler(self):
        logging.getLogger().removeHandler(self.fh)
        self.logger.removeHandler(self.sh)
        self.fh.close()
        self.logger.removeHandler(self.fh)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.remove_handler()
        return False


def create_logger(logger_name='', file_name='', log_path=None, clevel=None, flevel=None):
    if log_path is None:
        log_path = paths.TEST_DIR
    if clevel is None:
        clevel = _console_level
    if flevel is None:
        flevel = _file_level
    if not os.path.exists(log_path):
        os.makedirs(log_path)

    log_file = os.path.join(log_path, 'test.log')
    if os.path.exists(log_file):
        os.remove(log_file)

    if not logger_name:
        logger_name = file_name or 'test'
    logger = Logger(logger_name, log_file, clevel=clevel, Flevel=flevel)
    return logger

