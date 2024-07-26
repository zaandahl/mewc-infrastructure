#!/bin/bash

# Fetch all the reservations
flavors=$(openstack flavor list --long | grep "reservation" | awk '{print $2}')

# Filter out flavors with active instances and pick the first one
for flavor in $flavors; do
    instances_using_flavor=$(openstack server list --flavor $flavor -f value -c ID)
    if [ -z "$instances_using_flavor" ]; then
        echo "{\"flavor_id\": \"$flavor\"}"
        exit 0
    fi
done

# If there are no available flavors, return an empty JSON
echo '{"flavor_id": ""}'
exit 0
