#!/usr/bin/env bats

@test "shell scripts parse" {
  run bash -n scripts/install.sh scripts/start-audio-loopback.sh scripts/start-cavasik-kiosk.sh
  [ "$status" -eq 0 ]
}
