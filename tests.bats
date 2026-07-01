#!/usr/bin/env bats

@test "shell scripts parse" {
  run bash -n scripts/install.sh scripts/start-audio-loopback.sh scripts/start-cavasik-kiosk.sh scripts/start-now-playing-overlay.sh scripts/check-cavasik.sh
  [ "$status" -eq 0 ]
}

@test "python modules compile" {
  run python3 -m compileall rca_visualizer
  [ "$status" -eq 0 ]
}

@test "audio detection unit tests pass" {
  run python3 tests/test_audio_detection.py
  [ "$status" -eq 0 ]
}
