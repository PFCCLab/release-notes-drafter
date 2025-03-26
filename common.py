# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import locale
import os
import re
import subprocess
from collections import namedtuple
from pathlib import Path
from CheckPRTemplate import *

import requests

PR_Category = [
    'User Experience',
    'Execute Infrastructure',
    'Operator Mechanism',
    'CINN',
    'Custom Device',
    'Performance Optimization',
    'Distributed Strategy',
    'Parameter Server',
    'Communication Library',
    'Auto Parallel',
    'Inference',
    'Environment Adaptation',
    'Others',
]

PR_Types = [
    'New features',
    'Bug fixes',
    'Improvements',
    'Performance',
    'BC Breaking',
    'Deprecations',
    'Docs',
    'Devs',
    'Not User Facing',
    'Security',
    'Deprecations',
    'Others',
]


Features = namedtuple(
    'Features',
    [
        'title',
        'body',
        'pr_number',
        'files_changed',
        'labels',
        'author',
        'accepters',
        'pr_category',
        'pr_types'
    ],
)


def dict_to_features(dct):
    return Features(
        title=dct['title'],
        body=dct['body'],
        pr_number=dct['pr_number'],
        files_changed=dct['files_changed'],
        labels=dct['labels'],
        author=dct['author'],
        accepters=tuple(dct['accepters']),
    )


def features_to_dict(features):
    return dict(features._asdict())


def run(command):
    """Returns (return-code, stdout, stderr)"""
    p = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
    )
    output, err = p.communicate()
    rc = p.returncode
    enc = locale.getpreferredencoding()
    output = output.decode(enc)
    err = err.decode(enc)
    return rc, output.strip(), err.strip()


def commit_body(commit_hash):
    cmd = f'git log -n 1 --pretty=format:%b {commit_hash}'
    ret, out, err = run(cmd)
    return out if ret == 0 else None


def commit_title(commit_hash):
    cmd = f'git log -n 1 --pretty=format:%s {commit_hash}'
    ret, out, err = run(cmd)
    return out if ret == 0 else None


def commit_files_changed(commit_hash):
    cmd = f'git diff-tree --no-commit-id --name-only -r {commit_hash}'
    ret, out, err = run(cmd)
    return out.split('\n') if ret == 0 else None


def parse_pr_number(body, commit_hash, title):
    regex = r'\(#([0-9]+)\)'
    matches = re.findall(regex, title)
    if len(matches) == 0:
        print(
            f'[{commit_hash}: {title}] Could not parse PR number, ignoring PR'
        )
        return None
    if len(matches) > 1:
        print(
            f'[{commit_hash}: {title}] Got two PR numbers, using the first one'
        )
        return matches[0]
    return matches[0]


# to get a token, Go to github.com Settings -> Developer Settings -> Personal Access Tokens
# and generate a token with public_repo access.
# and add this token to your '~/.gh_tokenrc' configuration:
# github_oauth = <YOUR_GITHUB_TOKEN>


def get_ghstack_token():
    pattern = 'github_oauth = (.*)'
    with open(Path('~/.gh_tokenrc').expanduser(), 'r+') as f:
        config = f.read()
    matches = re.findall(pattern, config)
    if len(matches) == 0:
        raise RuntimeError("Can't find a github oauth token")
    return matches[0]


token = get_ghstack_token()
headers = {"Authorization": f"token {token}"}


def run_query(query):
    request = requests.post(
        'https://api.github.com/graphql', json={'query': query}, headers=headers
    )
    if request.status_code == 200:
        return request.json()
    else:
        raise Exception(
            "Query failed to run by returning code of {}. {}".format(
                request.status_code, query
            )
        )


def getPRTemplate(body):
    PR_dic = {}
    note = r'<!-- TemplateReference: https://github.com/PaddlePaddle/Paddle/wiki/PULL-REQUEST-TEMPLATE--REFERENCE -->\r\n|<!-- Demo: https://github.com/PaddlePaddle/Paddle/pull/24810 -->\r\n|<!-- One of \[ User Experience \| Execute Infrastructure \| Operator Mechanism \| CINN \| Custom Device \| Performance Optimization \| Distributed Strategy \| Parameter Server \| Communication Library \| Auto Parallel \| Inference \| Environment Adaptation \| Others \] -->|<!-- One of \[ New features \| Bug fixes \| Improvements \| Performance \| BC Breaking \| Deprecations \| Docs \| Devs \| Not User Facing \| Security \| Deprecations \| Others \] -->|<!-- Describe what you’ve done -->'
    if body is None:
        body = ''
    body = re.sub(note, "", body)
    body = re.sub("\r\n", "", body)
    type_end = body.find('### PR Types')
    changes_end = body.find('### Description')
    PR_dic['PR Category'] = body[len('### PR Category') : type_end]
    PR_dic['PR Types'] = body[type_end + len('### PR Types') : changes_end]
    pr_category_res = ''
    pr_type_res = ''
    TemplateInfo = dict()

    for key in PR_dic:
        test_list = PR_Category if key == 'PR Category' else PR_Types
        test_list_lower = [l.lower() for l in test_list]
        value = PR_dic[key].strip().split(',')
        TemplateInfo[key] = value

    return TemplateInfo['PR Category'][0], TemplateInfo['PR Types'][0]


def github_data(pr_number):
    query = (
        """
    {
      repository(owner: "PaddlePaddle", name: "Paddle") {
        pullRequest(number: %s ) {
          author {
            login
          }
          body
          reviews(last: 5, states: APPROVED) {
            nodes {
              author {
                login
              }
            }
          }
          labels(first: 10) {
            edges {
              node {
                name
              }
            }
          }
        }
      }
    }
    """
        % pr_number
    )
    query = run_query(query)

    edges = query['data']['repository']['pullRequest']['labels']['edges']
    labels = [edge['node']['name'] for edge in edges]
    author = query['data']['repository']['pullRequest']['author']['login']
    nodes = query['data']['repository']['pullRequest']['reviews']['nodes']
    body = query['data']['repository']['pullRequest']['body']
    
    # using set to dedup multiple accepts from same accepter
    accepters = {node["author"]["login"] for node in nodes if node and node["author"]}
    accepters = tuple(sorted(accepters))

    return labels, author, accepters, body


def get_features(commit_hash):
    title, body, files_changed = (
        commit_title(commit_hash),
        commit_body(commit_hash),
        commit_files_changed(commit_hash),
    )
    pr_number = parse_pr_number(body, commit_hash, title)
    labels = []
    author = ""
    accepters = tuple()
    if pr_number is not None:
        labels, author, accepters, pr_template = github_data(pr_number)
        pr_category, pr_types = getPRTemplate(pr_template)
    result = Features(
        title, body, pr_number, files_changed, labels, author, accepters, pr_category, pr_types
    )
    return result


_commit_data_cache = None


def get_commit_data_cache(path='results/data.json'):
    global _commit_data_cache
    if _commit_data_cache is None:
        _commit_data_cache = _CommitDataCache(path)
    return _commit_data_cache


class _CommitDataCache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            self.data = self.read_from_disk()
        else:
            os.makedirs(Path(path).parent, exist_ok=True)

    def get(self, commit):
        if commit not in self.data.keys():
            # Fetch and cache the data
            self.data[commit] = get_features(commit)
            self.write_to_disk()
        return self.data[commit]

    def read_from_disk(self):
        with open(self.path, 'r') as f:
            data = json.load(f)
            data = {
                commit: dict_to_features(dct) for commit, dct in data.items()
            }
        return data

    def write_to_disk(self):
        data = {
            commit: features._asdict() for commit, features in self.data.items()
        }
        with open(self.path, 'w') as f:
            json.dump(data, f)
