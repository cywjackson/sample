#! /bin/bash

# default values
year=`date +%Y`
vtype="hvm"
dtype="instance-store"
btype="standard"
region="us-west-2"
count=5
usage="usage: get_latest_ami.sh -y (year) -v (virtualization-type) -d (root-device-type) -b (block-device-mapping.volume-type) -r (region) -c (return size)"

while getopts "hy:v:d:b:r:c:" opt; do
    case $opt in
		h) echo $usage
		exit 0;;
        y) year=$OPTARG ;;  
        v) vtype=$OPTARG ;;  
        d) dtype=$OPTARG ;;  
        b) btype=$OPTARG ;;  
        r) region=$OPTARG ;;  
        c) count=$OPTARG ;; 
		*) echo $usage
		exit 1;;
    esac
done

device_filter="Name=root-device-type,Values=$dtype"
if [ "$dtype" == "ebs" ]; then
    device_filter=$device_filter" Name=block-device-mapping.volume-type,Values=$btype"
fi

let count=-$count-1

FILTERS=("Name=name,Values=amzn-ami-hvm*,amzn-ami-pv*" "Name=owner-alias,Values=amazon" "Name=state,Values=available" "Name=architecture,Values=x86_64" "Name=virtualization-type,Values=$vtype" $device_filter)
#QUERY=('sort_by(Images[?CreationDate>=`$year-01-01`],&CreationDate)[-1]')
QUERY=("sort_by(Images[?CreationDate>=\`$year-01-01\`] |[?CreationDate<=\`$year-12-31\`] | [?contains(Name, \`rc\`) == \`false\`],&CreationDate)[-1:$count:-1].{Name:Name, ImageId:ImageId}")
aws --region $region ec2 describe-images --filters "${FILTERS[@]}" --query "${QUERY[@]}"
