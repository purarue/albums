from pathlib import Path
from setuptools import setup, find_packages


requirements = Path("requirements.txt").read_text().strip().splitlines()
pkg = "nextalbums"

if __name__ == "__main__":
    setup(
        name=pkg,
        url="https://github.com/purarue/albums",
        use_scm_version={
            "local_scheme": "dirty-tag",
        },
        zip_safe=False,
        author="purarue",
        description=("my personal album system"),
        license="MIT",
        packages=find_packages(include=[pkg]),
        entry_points={"console_scripts": ["nextalbums = nextalbums.__main__:main"]},
        package_data={pkg: ["py.typed"]},
        install_requires=requirements,
    )
