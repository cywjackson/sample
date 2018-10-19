import datetime
import logging, sys
import time

import boto3
from botocore.exceptions import WaiterError, ClientError

_LOG = logging.getLogger(__name__) # NOTE: add 'extra=d' in any _LOG function)

# extra format for env
FORMAT = '%(asctime)s %(levelname)7s %(name)s:%(lineno)d -  %(funcName)s - [%(env)s] - %(message)s' # modified from etc/logging.yaml
formatter = logging.Formatter(fmt=FORMAT)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(formatter)
_LOG.addHandler(handler)
_LOG.propagate = False
d = {'env': '-'} # To be overrided by methods that has specific env
_DELIMITER = "-"

# common configs:
_base_instance_id = 'i-1234' # hardcode? ever change? query by tag?
_prefix = "prefix"
_lc_prefix = "lc"
_asg_prefix = "asg"
_elb = "elb_name" # diff region share the same elb name, but it will be diff elb per region

_config = {}
_config['prod'] = {'env': 'prod', 'subnets': 'subnet-zone-a,subnet-zone-b,subnet-zone-c', 'sg_group': 'sg-1234', 'iam': 'prod-iam', 'session': boto3.session.Session(region_name='us-west-2')}
_config['frankfurt'] = {'env': 'frankfurt', 'subnets': 'subnet-zone-a,subnet-zone-b,subnet-zone-c', 'sg_group': 'sg-5678', 'iam': 'ff-iam', 'session': boto3.session.Session(region_name='eu-central-1')}

def start_instance():
    """ Start the base inatance.

    Raises:
        Caller to handle exception on failure.
    """
    _ec2_r = _config['prod']['session'].resource('ec2')
    _ec2_c = _config['prod']['session'].client('ec2')
    base_instance = _ec2_r.Instance(_base_instance_id)
    base_instance.start()
    _LOG.debug('Base instance %s started. Waiting for it to come up.', _base_instance_id, extra=d)
    base_instance.wait_until_running()
    _LOG.debug('Base instance %s up and running.', _base_instance_id, extra=d)

    waiter = _ec2_c.get_waiter('system_status_ok')
    waiter.wait(InstanceIds=[_base_instance_id])
    _LOG.debug('Base instance %s system_status_ok.', _base_instance_id, extra=d)


def stop_instance(skip_wait):
    """ Stop the base instance.

    The base instance is a Windows instance. AWS doc recommends making sure
    it's in a good state before shuting down.

    Args:
        skip_wait (boolean): To skip wait for the base instance status to be ok
            before stopping
    Raises:
        Caller to handle any exception on stopping failure.
    """
    _ec2_r = _config['prod']['session'].resource('ec2')
    _ec2_c = _config['prod']['session'].client('ec2')
    base_instance = _ec2_r.Instance(_base_instance_id)
    _LOG.debug('Waiting for base instance %s for system_status_ok.', _base_instance_id, extra=d)
    if not skip_wait:
        waiter = _ec2_c.get_waiter('system_status_ok')
        try:
            waiter.wait(InstanceIds=[_base_instance_id])
        except:
            # TODO An error is returned after wait 10 min. should we handle/log the err and continue to stop anyway?
            _LOG.error("error waiting on base instance %s to be ok. stopping anyway" , _base_instance_id, extra=d)

    _LOG.debug('Stopping base instance %s.', _base_instance_id, extra=d)
    base_instance.stop()
    # TODO: do we care? reuse flag? new flag?
    # base_instance.wait_until_stopped()


def deploy(user_name):
    image_name = _create_ami_image(user_name)
    launch_config_name = _DELIMITER.join((_lc_prefix, image_name))
    return _do_deploy(launch_config_name)


def _create_ami_image(user_name):
    """ Create an AMI based off the base instance.

    This will create the ami image based on the username in us-west-2, and make a copy in frankfurt.
    This method is blocked until image is available. The ami will be used for deploying new codes.
    Args:
        user_name (str): user_name from the caller, this is used to generated the image name
    Returns:
        str: image_name
    Raises:
        Caller to handle exception on failure.
    """
    current_datetime_s = datetime.datetime.strftime(datetime.datetime.now(), '%Y_%m_%d_%H_%M_%S')
    image_name = _DELIMITER.join((_prefix, user_name, current_datetime_s))

    global _config
    # create image in prod
    _ec2_c = _config['prod']['session'].client('ec2')
    _LOG.debug('creating ami image %s in prod.', image_name, extra=d )
    image_id = _ec2_c.create_image(
        InstanceId=_base_instance_id,
        Name=image_name,
        Description=image_name,
    )['ImageId']

    _wait_image(_config['prod'], image_id)
    _LOG.debug('created ami image %s: %s in prod.', image_id, image_name, extra=d )

    # copy to frankfurt, this is async, could be really slow
    # TODO: what if we have another env? should loop every thing but prod in the _config
    _LOG.debug('copying ami image %s to frankfurt.', image_name, extra=d )
    image_id_ff = _config['frankfurt']['session'].client('ec2').copy_image(
        SourceRegion='us-west-2',
        SourceImageId=image_id,
        Name=image_name,
        Description=image_name,
        Encrypted=False
    )['ImageId']

    _wait_image(_config['frankfurt'], image_id_ff)
    _LOG.debug('copied ami image %s: %s to frankfurt', image_id_ff, image_name, extra=d )

    # TODO: tag both prod and ff images
    
    # update the global config
    _config['prod']['image_id'] = image_id
    _config['frankfurt']['image_id'] = image_id_ff
    return image_name


def _wait_image(config, image_id):
    d = {'env': config['env']}
    # hacky workaround for boto3 bug/err
    # (eg: botocore.exceptions.WaiterError: Waiter ImageAvailable failed: Waiter encountered a terminal failure state)
    time.sleep(10)

    _ec2_c = config['session'].client('ec2')
    waiter = _ec2_c.get_waiter('image_available')
    # image creation for windows can take a long time, wait longer and only consider failure afterward
    # copy image to ff is even slower, so increase wait even longer
    for i in range(1, 11):
        try:
            _LOG.debug('Waiting for image %s to be available. Trying %s...', image_id, i, extra=d)
            waiter.wait(ImageIds=[image_id])
            break
        except WaiterError as e:
            if i == 10:
                raise e
            continue


def _do_deploy(launch_config_name):
    response = {}
    for key in _config.keys():
        old_asg_name, new_asg_name = _do_blue_green_deploy(_config[key], launch_config_name)
        response[key] = [old_asg_name, new_asg_name]
    return response


def _do_blue_green_deploy(config, launch_config_name):
    d = {'env': config['env']}
    """ Trigger blue/green deployment via swapping ASG with same ELB.
        1. create new lc
        2. create new asg  with new lc, but no elb.
        3. wait for new instances Healthy and InService
        4. remove scale in protection on new instances on new asg
        5. attach elb to new asg and update asg health check to elb
        5. wait for instance registered with elb
        6. find old asg
        7. deatach elb from old asg
        8. wait

        #TODO potential rollback step(s): Note: cleanup steps should be similar removing the oldest set (asg-lc-ami-snapshot)
        aws autoscaling update-auto-scaling-group --auto-scaling-group-name <old_asg_name> ...
        aws autoscaling attach-load-balancers --auto-scaling-group-name <old_asg_name>
        aws autoscaling detach-load-balancers --auto-scaling-group-name <new_asg_name> --load-balancer-names <elb_name> #Need wait?
        aws autoscaling update-auto-scaling-group --auto-scaling-group-name <new_asg_name> --min-size 0 #this step may terminate the instances, so below may not need, if need, then need to find instance ids
        aws autoscaling terminate-instance-in-auto-scaling-group  --instance-id <instance_id> --should-decrement-desired-capacity
        aws autoscaling terminate-instance-in-auto-scaling-group  --instance-id <instance_id> --should-decrement-desired-capacity
        aws autoscaling delete-auto-scaling-group --auto-scaling-group-name <new_asg_name>
        aws autoscaling delete-launch-configuration --launch-configuration-name <new_lc_name>
        aws ec2 describe-images --image-id <new_ami_id> #To get snapshot id
        aws ec2 deregister-image --image-id <new_ami_id>
        aws ec2 delete-snapshot --snapshot-id <new_snapshot_id>

    Args:
        image_id (sting): AMI ID, the AMI to be deployed.
        launch_config_name (string): the name of the LC which will contains the
            given AMI ID.
    Raises:
        Caller to handle exception on failure.

    Returns:
        str, str : previous autoscaling group name (or None), new autoscaling group name
    """
    _create_lc(config, launch_config_name)
    asg_name = _create_asg(config, launch_config_name)
    _wait_for_instances_healthy(config, asg_name)
    _remove_protection(config, asg_name)
    _attach_elb_to_asg(config, asg_name)
    # traffic starts flowing to new asg / instances after here
    # TODO: roll back instead of continue below if new instances failed ELB health check
    _wait_for_elb(config, _elb, asg_name, "InService")
    old_asg_name = _find_old_asg_name(config, _elb, asg_name)
    if old_asg_name:
        _detach_elb_from_old_asg(config, old_asg_name)
        _wait_for_elb(config, _elb, old_asg_name, "OutOfService")
    return old_asg_name, asg_name


def _create_lc(config, launch_config_name):
    d = {'env': config['env']}
    _LOG.debug("creating launch config %s", launch_config_name, extra=d)
    config['session'].client('autoscaling').create_launch_configuration(
        LaunchConfigurationName=launch_config_name,
        ImageId=config['image_id'],
        InstanceType='m3.large',
        SecurityGroups=[config['sg_group']],
        IamInstanceProfile=config['iam'] 
    )
    _LOG.debug('created launch config %s', launch_config_name, extra=d)


def _create_asg(config, launch_config_name):
    d = {'env': config['env']}
    """ Step 1: Create an Auto Scaling Group
        Step 2: Create Scaling Policies
        Step 3: Create CloudWatch Alarms

    Args:
        launch_config_name (string): the name of the LC which will contains the
            given AMI ID.
    Returns:
        str: the newly created auto scaling group name
    Raises:
        Caller to handle exception on failure.
    """
    _LOG.debug("creating autoscalinggroup with config %s", launch_config_name, extra=d)
    asg_name = _DELIMITER.join((_asg_prefix, launch_config_name.replace(_lc_prefix + _DELIMITER, '')))
    user_name = asg_name.split(_DELIMITER)[2]
    azs = [i['ZoneName'] for i in config['session'].client('ec2').describe_availability_zones()['AvailabilityZones']]
    _asg = config['session'].client('autoscaling')
    _asg.create_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        LaunchConfigurationName=launch_config_name,
        MinSize=2,
        MaxSize=4,
        DesiredCapacity=2,
        DefaultCooldown=300,
        AvailabilityZones=azs,
        HealthCheckGracePeriod=600,
        VPCZoneIdentifier=config['subnets'],
        TerminationPolicies=["OldestLaunchConfiguration", "OldestInstance", "Default"],
        NewInstancesProtectedFromScaleIn=True,
        Tags=[
            {
                "ResourceType": "auto-scaling-group",
                "ResourceId": asg_name,
                "PropagateAtLaunch": True,
                "Value": config['env'],
                "Key": "Environment"
            },
            {
                "ResourceType": "auto-scaling-group",
                "ResourceId": asg_name,
                "PropagateAtLaunch": True,
                "Value": _prefix,
                "Key": "role"
            },
            {
                "ResourceType": "auto-scaling-group",
                "ResourceId": asg_name,
                "PropagateAtLaunch": True,
                "Value": _DELIMITER.join((asg_name, config['env'])),
                "Key": "Name"
            },
            {
                "ResourceType": "auto-scaling-group",
                "ResourceId": asg_name,
                "PropagateAtLaunch": True,
                "Value": user_name,
                "Key": "owner"
            }
        ]
    )

    # Create Scaling Policies and CloudWatch Alarms for Scale Up
    scale_up_policy_arn = _asg.put_scaling_policy(
        AutoScalingGroupName=asg_name,
        PolicyName='high cpu',
        PolicyType='SimpleScaling',
        AdjustmentType='ChangeInCapacity',
        ScalingAdjustment=1,
        Cooldown=300
    )['PolicyARN']
    _cw_c = config['session'].client('cloudwatch')
    _cw_c.put_metric_alarm(
        AlarmName='awsec2-%s-CPU-Utilization' % asg_name,
        MetricName='CPUUtilization',
        Namespace='AWS/EC2',
        Statistic='Average',
        Dimensions=[
            {
                'Name': 'AutoScalingGroupName',
                'Value': asg_name
            },
        ],
        Period=300,
        EvaluationPeriods=1,
        Threshold=50.0,
        ComparisonOperator='GreaterThanOrEqualToThreshold'
    )

    # Create Scaling Policies and CloudWatch Alarms for Scale Down
    scale_down_policy_arn = _asg.put_scaling_policy(
        AutoScalingGroupName=asg_name,
        PolicyName='scale down',
        PolicyType='SimpleScaling',
        AdjustmentType='ChangeInCapacity',
        ScalingAdjustment=-1,
        Cooldown=300
    )['PolicyARN']
    _cw_c.put_metric_alarm(
        AlarmName='awsec2-%s-High-CPU-Utilization-scaledown' % asg_name,
        MetricName='CPUUtilization',
        Namespace='AWS/EC2',
        Statistic='Average',
        Dimensions=[
            {
                'Name': 'AutoScalingGroupName',
                'Value': asg_name
            },
        ],
        Period=300,
        EvaluationPeriods=1,
        Threshold=30.0,
        ComparisonOperator='LessThanOrEqualToThreshold'
    )
    _LOG.debug('created auto scaling group %s', asg_name, extra=d)
    return asg_name


def _remove_protection(config, asg_name):
    d = {'env': config['env']}
    """ Remove ScaleIn protection on instances. Should only called on those that's already healthy/inservice.
        Leaving protection on on instances seems to have undesired conflict with auto scaling policies

    Args:
        asg_name (string): autoscaling group name
    """

    instance_ids = _get_instance_ids(config, asg_name)
    _LOG.debug('removing ScaleIn protection from asg %s on instances %s', asg_name, instance_ids, extra=d)
    config['session'].client('autoscaling').set_instance_protection(
        AutoScalingGroupName=asg_name,
        InstanceIds=instance_ids,
        ProtectedFromScaleIn=False
    )
    _LOG.debug('removed ScaleIn protection from asg %s on instances %s', asg_name, instance_ids, extra=d)


def _wait_for_instances_healthy(config, asg_name):
    d = {'env': config['env']}
    """ Use the instances waiter class to wait for instances to be healthy
        then use describe_auto_scaling_instances to confirm instances'
        HealthStatus is HEALTHY and LifecycleState is InService

    Args:
        asg_name (string): autoscaling group name
    Raises:
        Caller to handle exception on failure.
    """
    new_instance_ids = []
    while len(new_instance_ids) == 0:
        time.sleep(10)  # wait a little b4 querying for new instance ids
        _LOG.debug('getting IDs of new instances for %s ', asg_name, extra=d)
        new_instance_ids = _get_instance_ids(config, asg_name)

    # TODO may need to wait to ensure have ids
    _ec2_c = config['session'].client('ec2')
    _LOG.debug('waiting for instance_running for new instances %s', new_instance_ids, extra=d)
    _ec2_c.get_waiter('instance_running').wait(InstanceIds=new_instance_ids)
    _LOG.debug('waiting for system_status_ok for new instances %s', new_instance_ids, extra=d)
    _ec2_c.get_waiter('system_status_ok').wait(InstanceIds=new_instance_ids)
    _LOG.debug('new instances %s  are ready.', new_instance_ids, extra=d)
    _wait_for_instances_inservice(config, asg_name)


def _attach_elb_to_asg(config, asg_name):
    d = {'env': config['env']}
    _LOG.debug('attaching elb %s to asg %s', _elb, asg_name, extra=d)
    _asg = config['session'].client('autoscaling')
    _asg.attach_load_balancers(
        AutoScalingGroupName=asg_name,
        LoadBalancerNames=[_elb]
    )
    _LOG.debug('updating asg %s healthcheck to elb ', asg_name, extra=d)
    _asg.update_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        HealthCheckType='ELB',
        HealthCheckGracePeriod=600
    )
    _LOG.debug('autoscaling group %s attached with elb %s', asg_name, _elb, extra=d)


def _find_old_asg_name(config, elb, new_asg_name):
    d = {'env': config['env']}
    """ 1. Get instances from elb (this included old and new instances)
        2. Get instances tag value for tag name  aws:autoscaling:groupName
        3. Filter and unique tag values for NOT new asg

    Args:
        elb (str): elb name
        new_asg_name (str): the new autoscaling group name to be filtered out from tag values
    Returns:
        str: old asg name, or None when no old asg name was found
    """
    all_instances = config['session'].client('elb').describe_instance_health(
        LoadBalancerName=elb
    )['InstanceStates']
    all_instance_ids = [ins['InstanceId'] for ins in all_instances]
    instance_tags = config['session'].client('ec2').describe_tags(
        Filters=[
            {
                'Name': 'resource-id',
                'Values': all_instance_ids,
            },
            {
                'Name': 'key',
                'Values': ['aws:autoscaling:groupName']
            }
        ]
    )['Tags']
    tag_values = set([tag['Value'] for tag in instance_tags])
    # assume a set of old and new asg name
    tag_values.discard(new_asg_name)
    return next(iter(tag_values), None)


def _detach_elb_from_old_asg(config, old_asg_name):
    d = {'env': config['env']}
    _LOG.debug('detaching elb %s from old asg %s', _elb, old_asg_name, extra=d)
    config['session'].client('autoscaling').detach_load_balancers(
        AutoScalingGroupName=old_asg_name,
        LoadBalancerNames=[_elb]
    )
    _LOG.debug('detached elb %s from old asg %s', _elb, old_asg_name, extra=d)


def _suspend_and_terminate_old_asg(config, old_asg_name):
    d = {'env': config['env']}
    _LOG.debug('suspending AlarmNotification on old asg %s', old_asg_name, extra=d)
    _asg = config['session'].client('autoscaling')
    _asg.suspend_processes(
        AutoScalingGroupName=old_asg_name,
        ScalingProcesses=['AlarmNotification'],
    )
    old_instance_ids = _get_instance_ids(config, old_asg_name)
    _LOG.debug('old instances in old asg %s: %s', old_asg_name, old_instance_ids, extra=d)
    _LOG.debug('updating old asg %s min/max/desired size to 0', old_asg_name, extra=d )
    _asg.update_auto_scaling_group(
        AutoScalingGroupName=old_asg_name,
        MinSize=0,
        MaxSize=0,
        DesiredCapacity=0
    )
    _remove_protection(config, old_asg_name)
    #_LOG.debug('entering standby for old asg %s instances %s', old_asg_name, old_instance_ids, extra=d)
    #_asg.enter_standby(
    #    InstanceIds=old_instance_ids,
    #    AutoScalingGroupName=old_asg_name,
    #    ShouldDecrementDesiredCapacity=True
    #)
    return old_instance_ids


def cleanup(env_key, old_asg_name): #TODO
    """ 1. enter stand by + decreased desired cap for old asg
        2  short wait
        3. terminate old instances and asg
        4. ec2 waiter for instance termination
        4. stop base instance
        5. TODO: cleanup oldest asg, lc, ami, snapshots (limit 10?)
            a. query to find oldest asg
            b. get the launch config from the oldest asg
            c. get the ami id from lc
            d. get snapshotS from ami_id
            e. delete asg
            f. delete lc
            g. deregister ami
            h. delete snapshots

    """
    if old_asg_name:
        old_instance_ids = _suspend_and_terminate_old_asg(_config[env_key],old_asg_name)
        time.sleep(60)  # TODO: better wait style: ELB registration takes time
        #_terminate(_config[env_key], old_instance_ids, old_asg_name)

    stop_instance(False)


def _terminate(config, old_instance_ids, old_asg_name):
    d = {'env': config['env']}
    if old_asg_name:
        _LOG.debug("terminating old asg %s instances %s", old_asg_name, old_instance_ids, extra=d)
        _asg = config['session'].client('autoscaling')
        for instance_id in old_instance_ids:
            _asg.terminate_instance_in_auto_scaling_group(
                InstanceId=instance_id,
                ShouldDecrementDesiredCapacity=True
            )
        time.sleep(10)
        _ec2_c = config['session'].client('ec2')
        for i in range(1, 11):
            try:
                _LOG.debug('going to wait for terminating instances %s to be successful. trying %s...', old_instance_ids, i, extra=d) 
                _ec2_c.get_waiter('instance_terminated').wait(InstanceIds=old_instance_ids)
                break
            except WaiterError as e:
                if i == 10: 
                    raise e
                continue


def _get_instance_ids(config, asg_name):
    d = {'env': config['env']}
    """ Helper method to return current instance ids on an autoscaling group

    Args:
        asg_name (str): autoscaling group name

    Returns:
        list: List of instance ids (str) in the given autoscaling group,
                empty list if there is no instance in the given group or empty group name
    """

    if asg_name:
        ag = config['session'].client('autoscaling').describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        if len(ag['AutoScalingGroups']) > 0:
            return [ins['InstanceId'] for ins in ag['AutoScalingGroups'][0]['Instances']]
    return []


def _wait_for_instances_inservice(config, asg_name):
    d = {'env': config['env']}
    """ LifecycleState: 'Pending'|'Pending:Wait'|'Pending:Proceed'|'Quarantined'|'InService'|'Terminating'|
            'Terminating:Wait'|'Terminating:Proceed'|'Terminated'|'Detaching'|'Detached'|'EnteringStandby'|'Standby'
        HealthStatus: 'Healthy'|'Unhealthy'
    """
    new_instance_ids = _get_instance_ids(config, asg_name)
    _asg = config['session'].client('autoscaling')
    num_attempts = 0
    while num_attempts < 40:
        new_ins = _asg.describe_auto_scaling_instances(
            InstanceIds=new_instance_ids
        )['AutoScalingInstances']
        healthy_count = 0
        for ins in new_ins:
            if (ins['HealthStatus'] != 'HEALTHY') or (ins['LifecycleState'] != 'InService'):
                _LOG.debug('instance %s not healthy: (%s, %s).',  
                    ins['InstanceId'], ins['HealthStatus'], ins['LifecycleState'], extra=d)
                break
            else:
                healthy_count += 1
        if healthy_count == len(new_ins):
            break
        else:
            num_attempts += 1
            time.sleep(15)
    else:
        wait_too_long = 'Wait too long for %s asg %s to have healthy instances %s' % (config['env'], asg_name, new_instance_ids)
        raise WaiterError(name='CustomASGInstancesHealthy', reason=wait_too_long)

    _LOG.debug('auto scaling group %s instances %s Healthy and InService', asg_name, new_instance_ids, extra=d)


def _wait_for_elb(config, elb_name, asg_name, desired_state):
    d = {'env': config['env']}
    """ Wait until the desired_state ("InService"|"OutOfService") of the instances of the asg in the elb is reached

    Args:
        elb_name (str): the elastic load balancer to check against
        asg_name (str): the auto scaling group to check against
        desired_state (str): the desired state of the instances to be in the elb

    Raises:
        Exception when the given desired_state is neither "InService" nor "OutOfService"
        WaitError when wait too long
    """

    if desired_state != "InService" and desired_state != "OutOfService":
        raise Exception("desired_state can only be InService or OutOfService'")

    instance_ids_list = _get_instance_ids(config, asg_name)
    _elb_c = config['session'].client('elb')
    num_attempts = 0
    while num_attempts < 40:
        # Note: We will get "ClientError" with "InvalidInstance" error,
        # especially right b4 attaching for "InService".  #For now just catch and
        # log it, then retry. Not consiering it an error and will not raise back
        # to the caller.
        try:
            states = _elb_c.describe_instance_health(
                LoadBalancerName=elb_name,
                Instances=[{'InstanceId': id} for id in instance_ids_list]
            )
            desired_state_count = 0
            for state in states['InstanceStates']:
                if state['State'] != desired_state:
                    _LOG.debug('instance %s NOT %s . Current state: %s', state['InstanceId'], desired_state, state['State'], extra=d)
                    break
                else:
                    _LOG.debug('instance %s is %s', state['InstanceId'], state['State'], extra=d)
                    desired_state_count += 1
            if desired_state_count == len(instance_ids_list):
                break
            else:
                _LOG.debug('instance %s from auto scaling group %s registered with elb %s NOT in %s yet. Tried %i time.', state['InstanceId'] , asg_name, elb_name, desired_state, num_attempts + 1 , extra=d) # don't wanna rely on logging to increment the actual counter, but then don't wanna display "Tried 0 time"
                num_attempts += 1
                time.sleep(15)
        except ClientError as err:
            _LOG.debug('checking instances %s with elb %s for %s failed on edge case. %s', instance_ids_list, elb_name, desired_state, err, extra=d)
            time.sleep(15)
    else:
        wait_too_long = 'Wait too long for %s instances %s to be in %s in %s' % (config['env'], instance_ids_list, desired_state, elb_name)
        raise WaiterError(name='CustomASGInstancesHealthy', reason=wait_too_long)



if __name__ == '__main__':
    _LOG = logging.getLogger('test')
    FORMAT = '%(asctime)s %(levelname)7s %(name)s:%(lineno)d -  %(funcName)s - [%(env)s] - %(message)s' # modified from etc/logging.yaml
    formatter = logging.Formatter(fmt=FORMAT)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    _LOG.addHandler(handler)
    _LOG.propagate = False
    d = {'env': '-'} # To be overrided by methods that has specific env

    start_instance()
    response = deploy('local.test')
    _LOG.debug("testing, new asg should be deployed. If execute by api, email would be sent out. clean up next. Response: %s ", _DELIMITER, extra=d)
    for key in response.keys():
        cleanup(key, response[key][0])

