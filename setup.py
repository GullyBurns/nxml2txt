from setuptools import setup
import os
import sys

data_dir = os.path.join(sys.prefix, "local/lib/python2.7/dist-package/my_module")
data_files   = [ ("my_module",  [os.path.join(data_dir, "data1"),
                                 os.path.join(data_dir, "data2")])]
setup(
    name='nxml_2_txt',
    version='1.0.1',
    packages=['src'],
    data_dir = os.path.join(sys.prefix, "local/lib/python3.7/site-packages/data/"),
    data_files=[("data", [os.path.join(data_dir, "entities.dat'")])],
    url='https://github.com/GullyBurns/nxml2txt',
    license='MIT ',
    author='orig. Sampo Pyysalo; updated: Gully Burns',
    author_email='gully.burns@chanzuckerberg.com',
    description='XML formatted full-text articles to text format conversion.',
    install_requires=[
          'lxml',
      ]
)
