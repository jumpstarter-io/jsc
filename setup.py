import os
import os.path
import sys

from setuptools import setup, find_packages

from jsc import __version__, __description__, __long_description__


def read(fname):
    path = os.path.join(os.path.dirname(__file__), fname)
    f = open(path)
    return f.read()

install_requires = []
pyversion = sys.version_info[:2]
if pyversion < (2, 7) or (3, 0) <= pyversion <= (3, 1):
    install_requires.append('argparse')


setup(
    name='jsc',
    version=__version__,
    packages=find_packages(),
    author='Jumpstarter',
    author_email='team@jumpstarter.io',
    description=__description__,
    long_description=__long_description__,
    license='MIT',
    keywords='jumpstarter js jsc deploy',
    url="https://github.com/jumpstarter-io/jsc",
    zip_safe=True,
    include_package_data=True,

    install_requires=[
        'setuptools',
        'remoto',
        'choice',
        'docopt'
        ] + install_requires,

    entry_points={
        'console_scripts': [
            'jsc = jsc.main:main',
            ]
        }
    )
