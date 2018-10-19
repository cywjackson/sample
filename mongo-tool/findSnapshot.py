#! /usr/bin/env python
from datetime import timedelta, datetime
from pytz import timezone
import pytz
import boto3
import os
import urllib2

date_fmt="%Y-%m-%d-%H"
pacific=timezone('America/Los_Angeles')

restore_from = os.environ.get("RESTORE_FROM")
restore_from_dt = pacific.localize(datetime.strptime(restore_from, date_fmt))
temp_db = os.environ.get("TEMP_DB")
home_path = os.environ.get("HOME")
boto3.setup_default_session(region_name="us-west-2")
ec2_resource = boto3.resource('ec2')
ec2_client = boto3.client('ec2')

def func(x):
    d = x[0]
    delta =  d - restore_from_dt if d > restore_from_dt else timedelta.max
    return delta


def find_snapshot_to_date(restore_datetime):
    mongo_snapshots=ec2_resource.snapshots.filter(
        Filters=[
            {'Name': 'status', 'Values': ['completed']},
            {'Name': 'tag:role', 'Values': ['mongo']},
            {'Name': 'tag:environment', 'Values': ['prod']}
        ])
    ms_list = [(ms.start_time, ms) for ms in mongo_snapshots.all() ]
    snapshot = min(ms_list, key = func)
    print "%s - %s " % (snapshot[0].strftime('%c %Z') , snapshot[1])
    return snapshot


def create_volume(snapshot):
    vol=ec2_resource.create_volume(
        DryRun=False,
        SnapshotId=snapshot[1].id,
        VolumeType="gp2",
        AvailabilityZone=urllib2.urlopen('http://169.254.169.254/latest/meta-data/placement/availability-zone').read()
    )           
    print "creating %s" % vol.id
    ec2_client.get_waiter('volume_available').wait(VolumeIds=[vol.id])
    print "created %s" % vol.id
    vol.create_tags(
        Tags=[
            {
                'Key': 'Name',
                'Value': '%s_%s' % (temp_db , restore_from)
            },
            {
                'Key': 'role',
                'Value': 'mongo-restore'
            },
            {
                'Key': 'environment',
                'Value': 'ops'
            }
        ]
    )
    return vol


def attach_volume(vol):
    #TODO: user current instance?
    current_instance=ec2_resource.Instance(urllib2.urlopen('http://169.254.169.254/latest/meta-data/instance-id').read())
    #TODO: available_device=find_next_device(current_instance.block_device_mapping)
    available_device='/dev/xvdz'
    print "attaching %s to %s on %s" % (vol.id, current_instance, available_device)
    current_instance.attach_volume(
        DryRun=False,
        VolumeId=vol.id,
        Device=available_device
    )
    ec2_client.get_waiter('volume_in_use').wait(VolumeIds=[vol.id])
    print "attached %s to %s on %s" % (vol.id, current_instance, available_device)
    return available_device


#def find_next_device(block_device_mapping):


def main():
    snapshot = find_snapshot_to_date(restore_from_dt)
    volume = create_volume(snapshot)
    device = attach_volume(volume)
    # write the device to a file so later can read this file for the mount
    with open(home_path+'/'+temp_db + ".txt", 'wb') as fh:
        fh.write(device)


if __name__ == '__main__':
    main()
