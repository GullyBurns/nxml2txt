from setuptools import setup

setup(
    name='nxml_2_txt',
    version='1.0.1',
    packages=['src','data'],
    url='https://github.com/GullyBurns/nxml2txt',
    license='MIT ',
    author='orig. Sampo Pyysalo; updated: Gully Burns',
    author_email='gully.burns@chanzuckerberg.com',
    description='XML formatted full-text articles to text format conversion.',
    install_requires=[
          'lxml',
      ]
)
