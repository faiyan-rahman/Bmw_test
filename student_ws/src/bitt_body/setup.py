from setuptools import setup

package_name = 'bitt_body'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='BITT Team',
    maintainer_email='team@example.com',
    description='BITTs Koerpersprache (Lichter, Hupe)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'body = bitt_body.body:main',
        ],
    },
)
