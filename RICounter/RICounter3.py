#!/usr/bin/env python

"""
RICounter - outputs RI balance for the current AWS region

negative balances indicate excess reserved instances
positive balances indicate instances that are not falling under RIs
"""

from collections import Counter;
from os import getenv;

import boto3

def custom_sort(key1, key2):
    weightd = {
        "nano":0.25,
        "micro":0.5,
        "small":1,
        "medium":2,
        "large":4,
        "xlarge":8,
        "2xlarge":16,
        "4xlarge":32,
        "8xlarge":64,
        "10xlarge":80,
        "16xlarge":128,
        "32xlarge":256
    }
    return int(4 * (weightd[key1.split('.')[1]] - weightd[key2.split('.')[1]]))


DISABLED_REGIONS = ['cn-north-1', 'us-gov-west-1'];

ec3 = boto3.client('ec2')
regions = [r['RegionName'] for r in ec3.describe_regions()['Regions']]

for region in sorted(regions):
    if region in DISABLED_REGIONS:
        continue;

    print "==== %s:" % region
    try:
        ec3=boto3.client('ec2', region_name=region)
        ec3r=boto3.resource('ec2', region_name=region)
        reservations = ec3r.instances.filter(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    except Exception as e:
        logging.warning("Could not get instance reservations for region: " + region + " (" + e.message + ")");
        continue;

    instances=[{'type':i.instance_type, 'family': i.instance_type.split('.')[0]} for i in reservations if i.platform == None and i.spot_instance_request_id == None]

    instance_counter = Counter(i['type'] for i in instances) #eg: c3.2xlarge
    instance_family_counter = Counter(i['family'] for i in instances) #eg: c3

    familyDict = {}
    for k in sorted(instance_family_counter.keys()):
        key_array = []
        for i in instances:
            if i['family'] == k:
                key_array.append(i['type'])
        familyDict[k] = key_array

    for k in sorted(instance_family_counter.keys()):
        print "\t---- %s ----" % k
        for ri in ec3.describe_reserved_instances(Filters=[{'Name':'product-description', 'Values':['Linux/UNIX']}, {'Name':'state', 'Values':['active']}, {'Name': 'instance-type','Values':[k + '*']}])['ReservedInstances']:
            instance_counter.subtract({ri['InstanceType']: ri['InstanceCount']});
        for key in sorted(instance_counter.keys(), cmp=custom_sort):
            if key in familyDict[k]:
                print "\t%s\t%d" % (key, instance_counter[key]);
    print "\t---- actual instance counts per family ----"
    for key in sorted(instance_family_counter.keys()):
        print "\t%s\t%d" % (key, instance_family_counter[key]);

