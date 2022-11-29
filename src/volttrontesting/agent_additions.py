# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

# TODO: Need to update this file to use correct names for modular packages as they become available.
from volttron.client.known_identities import VOLTTRON_CENTRAL, VOLTTRON_CENTRAL_PLATFORM


def add_volttron_central(wrapper, config=None, **kwargs):
    config_dict = {
        # The agentid is used during display on the VOLTTRON central platform
        # it does not need to be unique.
        "agentid": "Volttron Central",

        # By default the webroot will be relative to the installation directory
        # of the agent when it is installed.  One can override this by specifying
        # the root directory here.
        # "webroot": "path/to/webroot",

        # Authentication for users is handled through a naive password algorithm
        # import hashlib
        # hashlib.sha512(password).hexdigest() where password is the plain text password.
        "users": {
            "reader": {
                "password": "2d7349c51a3914cd6f5dc28e23c417ace074400d7c3e176bcf5da72fdbeb6ce7ed767ca00c6c1fb754b8df5114fc0b903960e7f3befe3a338d4a640c05dfaf2d",
                "groups": [
                    "reader"
                ]
            },
            "admin": {
                "password": "c7ad44cbad762a5da0a452f9e854fdc1e0e7a52a38015f23f3eab1d80b931dd472634dfac71cd34ebc35d16ab7fb8a90c81f975113d6c7538dc69dd8de9077ec",
                "groups": [
                    "admin"
                ]
            },
            "dorothy": {
                "password": "cf1b67402d648f51ef6ff8805736d588ca07cbf018a5fba404d28532d839a1c046bfcd31558dff658678b3112502f4da9494f7a655c3bdc0e4b0db3a5577b298",
                "groups": [
                    "reader, writer"
                ]
            }
        }
    }

    if config is not None:
        config_dict = config

    print('Adding vc to {}'.format(wrapper.vip_address))
    agent_uuid = wrapper.install_agent(
        config_file=config_dict,
        agent_dir="volttron-central",
        vip_identity=VOLTTRON_CENTRAL,
        **kwargs
    )

    return agent_uuid


def add_listener(wrapper, config=None, vip_identity=None, **kwargs):
    print("Adding to {wrapper} a listener agent".format(wrapper=wrapper))
    config = config if config else {}
    agent_uuid = wrapper.install_agent(
        config_file=config,
        vip_identity=vip_identity,
        agent_dir="volttron-listener>=0.1.2a2",
        **kwargs
    )
    return agent_uuid


def add_volttron_central_platform(wrapper, config=None, **kwargs):
    print('Adding vcp to {}'.format(wrapper.vip_address))
    config = config if config else {}
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-central-platform",
        vip_identity=VOLTTRON_CENTRAL_PLATFORM
    )
    return agent_uuid


def add_sql_historian(wrapper, config, vip_identity='platform.historian',
                     **kwargs):
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-sqlite-historian",
        vip_identity=vip_identity,
        **kwargs
    )
    return agent_uuid


def add_mongo_historian(wrapper, config, vip_identity='platform.historian',
                       **kwargs):
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-mongo-historian",
        vip_identity=vip_identity,
        **kwargs
    )
    return agent_uuid


def add_sysmon(wrapper, config, **kwargs):
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-sysmon",
        **kwargs
    )
    return agent_uuid


def add_threshold_detection(wrapper, config, **kwargs):
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-threshold-detection",
        **kwargs
    )
    return agent_uuid


def add_emailer(wrapper, config, **kwargs):
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-emailer",
        **kwargs
    )
    return agent_uuid


def add_forward_historian(wrapper, config=None, **kwargs):
    config = config if config else {}
    agent_uuid = wrapper.install_agent(
        config_file=config,
        agent_dir="volttron-forward-historian",
        **kwargs
    )
    return agent_uuid
