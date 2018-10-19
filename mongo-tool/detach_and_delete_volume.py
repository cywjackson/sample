#! /usr/bin/env python
import boto3
import os
import urllib2

boto3.setup_default_session(region_name="us-west-2")
ec2_resource = boto3.resource('ec2')
ec2_client = boto3.client('ec2')

def find_volume():
    vol=ec2_resource.volumes.filter(
        Filters=[
            {
                'Name': 'attachment.instance-id',
                'Values': [urllib2.urlopen('http://169.254.169.254/latest/meta-data/instance-id').read()]
            },
            {
                'Name': 'attachment.device',
                'Values': ['/dev/xvdz'] #TODO: what if > 1 device
            }
        ]
    )
    print "found volume to detach: %s" % list(vol)[0]
    return list(vol)[0]


def detach_volume(vol):
    vol.detach_from_instance()
    ec2_client.get_waiter('volume_available').wait(VolumeIds=[vol.id])
    print "detached %s " % vol.id


def delete_volume(vol):
    vol.delete()
    ec2_client.get_waiter('volume_deleted').wait(VolumeIds=[vol.id])
    print "deleted %s" % vol.id


def main():
    volume = find_volume()
    detach_volume(volume)
    delete_volume(volume)


if __name__ == '__main__':
    main()
