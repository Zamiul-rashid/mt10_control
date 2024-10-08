from setuptools import find_packages, setup

package_name = 'mt10_control'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='mahir',
    maintainer_email='mahir@todo.todo',
    description='TODO: Package description',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'serial_control = mt10_control.serial_control:main',
            'autonomous = mt10_control.autonomous_v1:main',
            'gps_pub = mt10_control.gps_pub:main'

        ],
    },
)
