#!/bin/sh
set -eu
python3 /opt/oma-office/test_metadata.py -v
python3 /opt/oma-office/test_render.py -v
python3 /opt/oma-office/test_manager.py -v
(cd /opt/oma-office/slides && python3 -m unittest test_projects test_protocol -v)
(cd /opt/oma-tools && python3 -m unittest test_runtime test_protocol test_provider_http -v)
