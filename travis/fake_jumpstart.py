#!/usr/bin/env python2
# coding=utf-8

"""
Fakes a subset of capabilities of jumpstart/pacman for use in tests only

Usage:
    jumpstart -S [-y] PACKAGES...

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


def get_installed_packages():
    if os.path.exists(INSTALLED_PACKAGES_FILE):
        with open(INSTALLED_PACKAGES_FILE) as f:
            return json.loads(f.read())
    return {}


def update_package_list(package):
    packages = get_installed_packages()
    packages[package] = AVAILABLE_PACKAGES[package]
    with open(INSTALLED_PACKAGES_FILE, "wb+") as f:
        f.truncate(0)
        f.write(json.dumps(packages))


def sync_packages(packages):
    for package in packages:
        if package not in AVAILABLE_PACKAGES:
            print("error: target not found: {package}".format(package=package))
            return

    packages_w_ver = [AVAILABLE_PACKAGES[p] for p in packages]
    print("Packages ({num_pkgs}) {packages}".format(num_pkgs=len(packages), packages=" ".join(packages_w_ver)))
    for package in packages:
        update_package_list(package)


if __name__ == '__main__':
    try:
        args = docopt.docopt(__doc__)
        if args['-S']:
            sync_packages(args['PACKAGES'])
    except docopt.DocoptExit as e:
        print e.message
