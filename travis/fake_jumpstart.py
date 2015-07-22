#!/usr/bin/env python
# coding=utf-8

"""
Fakes a subset of capabilities of jumpstart/pacman for use in tests only

Usage:
    jumpstart -S [-y] [--noconfirm] PACKAGES...

Arguments:
    PACKAGES  packages to install
"""

import docopt
import os.path
import json


AVAILABLE_PACKAGES = {
    'nginx': 'nginx-1.4.6.1ubuntu3.1-13',
    'nodejs': 'nodejs-0.10.25~dfsg2.2ubuntu1-4',
    'php5': 'php5-5.5.9.1ubuntu4.5-11'
    }

# INSTALLED_PACKAGES_FILE = "/app/code/.pacman/db/jumpstart_installed"
INSTALLED_PACKAGES_FILE = "/tmp/jumpstart_installed"


def touch_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
        except IOError:
            pass

touch_dir(os.path.dirname(INSTALLED_PACKAGES_FILE))



if __name__ == '__main__':
    try:
        args = docopt.docopt(__doc__)
    except docopt.DocoptExit as e:
        print(str(e))
