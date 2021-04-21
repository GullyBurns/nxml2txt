from setuptools import setup
import os
import sys

data_dir = os.path.join(sys.prefix, "local/lib/python3.7/site-packages/data/")
data_files = [("data", [os.path.join(data_dir, "entities.dat")])]

setup(
    name='nxml_2_txt',
    version='1.0.1',
    packages=['nxml2txt'],
    package_dir={'nxml2txt': 'src/nxml2txt'},
    package_data={'nxml2txt': ['data/entities.dat']},
    url='https://github.com/GullyBurns/nxml2txt',
    license='MIT ',
    author='orig. Sampo Pyysalo; updated: Gully Burns',
    author_email='gully.burns@chanzuckerberg.com',
    description='XML formatted full-text articles to text format conversion.',
    install_requires=[
          'lxml',
      ]
)

