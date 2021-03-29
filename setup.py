import setuptools
import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="tfctl",
    version=os.getenv('GITHUB_REF').replace('refs/tags/', ''),
    author="Grauer W01f",
    author_email="grauerwf@gmail.com",
    description="A control tool for Hashicorp (c) Terraform projects",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/grauerwf/tfctl",
    packages=setuptools.find_packages(),
    entry_points={
        'console_scripts': [
            'tfctl=tfctl.tfctl:main',
        ],
    },
    install_requires=[
        'boto3',
        'PyYAML'
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)
