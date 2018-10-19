#TODO: use getopts to get options instead of parsed by arg
function mongo_restore() {
    if ! hash jq 2> /dev/null; then
        hash brew 2> /dev/null && brew install jq;
        hash apt-get 2> /dev/null && apt-get install jq;
    fi

    if [ $# -lt 2 ]; then
        echo "Usage: mongo_restore [collection to be restored] [date and hour (YYYY-MM-DD-hh) to restore the data from] [Filter (Optional)]" # TODO: mandatory option to check the restore is required by customer
        return
    fi

    COLLECTION_NAME="${1}"
    echo $COLLECTION_NAME
    shift
    RESTORE_FROM="${1}"
    echo $RESTORE_FROM
    # TODO: date validation
    # mac: date -j  -f "%Y-%m-%d-%H"  "2016-0-15-23"
    # linx (should be): date -d "2016-01-15-12" +"%Y-%m-%d-%H"
    shift
    #TODO: validate filter inside docker container via python lib
    FILTER=${1} # OPTIONAL, Limits the documents that mongorestore imports to only those documents that match the JSON document specified as '<JSON>'
    if [ -n "${FILTER}" ]; then # -n didn't work on shift'ed variable
        echo ${FILTER} | sed -e 's/ObjectId(\(.*\))/\1/g' | jq '.' > /dev/null
        if [ $? -ne 0 ]; then
            echo "invalid json filter: $FILTER"
            return # invalid JSON
        fi
        FILTER="--filter '${FILTER}'" # wrap <JSON> with ''
    fi
    echo $FILTER
    shift

    #TODO: add mount to no need to sudo? 
    #TODO: beautify the cmd, maybe using heredoc, quotes, line-break, be careful with space, needing to escape.
    #TODO: last "are u sure check?" ?
    GATEWAY_HOST=gateway.host
    TEMP_DB="${USER}${RANDOM}_${RESTORE_FROM}" #temp db that will have the data restored to. Data are NOT restored to 
    echo "Connecting to $(tput bold)${GATEWAY_HOST}$(tput sgr0) for restoring backup to $(tput bold)db_prod$TEMP_DB$(tput sgr0)..."
    CONTAINER_DATA="/data"
    MONGO_HOST="mongo.host"
    MONGO_TOOL_IMAGE="docker.repo/mongotool"
    SSH_CMD="ssh -t ${GATEWAY_HOST}"
    $SSH_CMD "/usr/bin/docker pull $MONGO_TOOL_IMAGE && 
    /usr/bin/docker run --rm -e RESTORE_FROM=$RESTORE_FROM -e HOME=/userhome -e TEMP_DB=$TEMP_DB -v \${HOME}:/userhome --entrypoint=/mongo-restore/findSnapshot.py $MONGO_TOOL_IMAGE && 
    mkdir -p $TEMP_DB && 
    sudo mount \`cat \${HOME}/$TEMP_DB.txt\` \${HOME}/$TEMP_DB && 
    /usr/bin/docker run --rm -v \${HOME}/$TEMP_DB:$CONTAINER_DATA $MONGO_TOOL_IMAGE $FILTER --noIndexRestore --noOptionsRestore -h $MONGO_HOST -d db_prod_$TEMP_DB -c $COLLECTION_NAME $CONTAINER_DATA/dump/db_prod/$COLLECTION_NAME.bson &&
    sudo umount \${HOME}/$TEMP_DB &&
    /usr/bin/docker run --rm --entrypoint=/mongo-restore/detach_and_delete_volume.py $MONGO_TOOL_IMAGE "
}
