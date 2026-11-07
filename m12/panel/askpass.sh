#!/bin/sh
# Called by ssh (SSH_ASKPASS) to supply the UnitV2 password from the environment.
printf '%s\n' "${UNITV2_PASS:-12345678}"
