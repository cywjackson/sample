#! /bin/bash

echo "mongo: $MONGO_HOST"
echo "tempdb: $TEMP_DB"
echo "coll: $COLLECTION_NAME"
echo "restore: $RESTORE_FROM"
echo "filter: $FILTER"

aws ec2 describe-snapshots --filters Name=tag:role,Values=mongo Name=status,Values=completed  --query "Snapshots[*].[SnapshotId,StartTime]" | jq -r '.[] | .[]' 
