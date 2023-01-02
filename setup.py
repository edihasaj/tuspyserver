"""
FastAPI Tus implementation
-------------
Implements the tus.io server-side file-upload protocol
visit https://tus.io for more information
"""

from setuptools import setup, find_packages

with open('README.md', 'r') as f:
    long_description = f.read()

setup(
    name='tuspyserver',
    version='1.0.6',
    description='TUS py protocol implementation in FastAPI',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Edi Hasaj',
    license='MIT',
    author_email='edihasaj@outlook.com',
    url='https://github.com/edihasaj/tuspy-fast-api',
    packages=find_packages(),
    platforms="any",
    include_package_data=True,
    install_requires=[
        'fastapi>=0.88.0',
        'starlette>=0.22.0',
        'pydantic>=1.10.4',
    ],
    classifiers=[
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules'
    ]
)
