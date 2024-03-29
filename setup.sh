#!/bin/bash

conda env create -f environment.yaml

apt-get update && apt-get install cmake libopenmpi-dev python3-dev zlib1g-dev

pip install stable-baselines
pip install gym

git config --global user.name "$NAME_GIT"
git config --global user.email "$EMAIL_GIT"
