#!/bin/bash

openstack flavor list --long | grep "reservation" | awk '{print $4}'

