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

import argparse
import csv
import dataclasses
import os
import pprint
import re
from collections import defaultdict
from pathlib import Path
from typing import List

from common import features_to_dict, get_commit_data_cache, run

"""
Example Usages

Said commitlist contains commits from a to b

    python commitlist.py --create_new HEAD^^^ HEAD

Update the existing commitlist to commit bfcb687b9c.

    python commitlist.py --update_to HEAD

"""


@dataclasses.dataclass(frozen=True)
class Commit:
    commit_hash: str
    category: str
    topic: str
    title: str
    pr_link: str
    author: str
    labels: str

    # This is not a list so that it is easier to put in a spreadsheet
    accepter_1: str
    accepter_2: str
    accepter_3: str

    merge_into: str = None

    def __repr__(self):
        return f'Commit({self.commit_hash}, {self.category}, {self.topic}, {self.title})'


commit_fields = tuple(f.name for f in dataclasses.fields(Commit))


class CommitList:
    # NB: Private ctor. Use `from_existing` or `create_new`.
    def __init__(self, path: str, commits: List[Commit]):
        self.path = path
        self.commits = commits

    @staticmethod
    def from_existing(path):
        commits = CommitList.read_from_disk(path)
        return CommitList(path, commits)

    @staticmethod
    def create_new(path, base_version, new_version):
        if os.path.exists(path):
            raise ValueError(
                'Attempted to create a new commitlist but one exists already!'
            )
        commits = CommitList.get_commits_between(base_version, new_version)
        return CommitList(path, commits)

    @staticmethod
    def read_from_disk(path) -> List[Commit]:
        with open(path) as csvfile:
            reader = csv.DictReader(csvfile)
            rows = []
            for row in reader:
                if row.get("new_title", "") != "":
                    row["title"] = row["new_title"]
                filtered_rows = {k: row.get(k, "") for k in commit_fields}
                rows.append(Commit(**filtered_rows))
        return rows

    def write_result(self):
        self.write_to_disk_static(self.path, self.commits)

    @staticmethod
    def write_to_disk_static(path, commit_list):
        os.makedirs(Path(path).parent, exist_ok=True)
        with open(path, 'w') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(commit_fields)
            for commit in commit_list:
                writer.writerow(dataclasses.astuple(commit))

    def keywordInFile(file, keywords):
        for key in keywords:
            if key in file:
                return True
        return False

    @staticmethod
    def gen_commit(commit_hash):
        feature_item = get_commit_data_cache().get(commit_hash)
        features = features_to_dict(feature_item)
        category, topic = CommitList.categorize(features)
        a1, a2, a3 = (features["accepters"] + ("", "", ""))[:3]
        if features["pr_number"] is not None:
            pr_link = f"https://github.com/PaddlePaddle/Paddle/pull/{features['pr_number']}"
        else:
            pr_link = None

        label_str = ",".join(features["labels"])

        return Commit(
            commit_hash,
            category,
            topic,
            features["title"],
            pr_link,
            features["author"],
            label_str,
            a1,
            a2,
            a3,
        )

    @staticmethod
    def categorize(features):
        title = features['title']
        labels = features['labels']
        category = features['pr_category']
        topic = features['pr_types']
        # category = 'Uncategorized'
        # topic = 'Untopiced'

        # We ask contributors to label their PR's appropriately
        # when they're first landed.
        # Check if the labels are there first.
        already_categorized = already_topiced = False
        for label in labels:
            if label.startswith('release notes: '):
                category = label.split('release notes: ', 1)[1]
                already_categorized = True
            if label.startswith('topic: '):
                topic = label.split('topic: ', 1)[1]
                already_topiced = True
        if already_categorized and already_topiced:
            return category, topic

        # TODO: have some herustic rules to assign category and topic.
        files_changed = features['files_changed']

        return category, topic

    @staticmethod
    def get_commits_between(base_version, new_version):
        cmd = f'git merge-base {base_version} {new_version}'
        rc, merge_base, _ = run(cmd)
        assert rc == 0

        # Returns a list of something like
        # b33e38ec47 Allow a higher-precision step type for Vec256::arange (#34555)
        cmd = f'git log --reverse --oneline {merge_base}..{new_version}'
        rc, commits, _ = run(cmd)
        assert rc == 0

        log_lines = commits.split('\n')
        hashes, titles = zip(
            *[log_line.split(' ', 1) for log_line in log_lines]
        )
        return [CommitList.gen_commit(commit_hash) for commit_hash in hashes]

    def filter(self, *, category=None, topic=None):
        commits = self.commits
        if category is not None:
            commits = [
                commit for commit in commits if commit.category == category
            ]
        if topic is not None:
            commits = [commit for commit in commits if commit.topic == topic]
        return commits

    def update_to(self, new_version):
        last_hash = self.commits[-1].commit_hash
        new_commits = CommitList.get_commits_between(last_hash, new_version)
        self.commits += new_commits

    def stat(self):
        counts = defaultdict(lambda: defaultdict(int))
        for commit in self.commits:
            counts[commit.category][commit.topic] += 1
        return counts


def create_new(path, base_version, new_version):
    commits = CommitList.create_new(path, base_version, new_version)
    commits.write_result()


def update_existing(path, new_version):
    commits = CommitList.from_existing(path)
    commits.update_to(new_version)
    commits.write_result()


def rerun_with_new_filters(path):
    current_commits = CommitList.from_existing(path)
    for i in range(len(current_commits.commits)):
        c = current_commits.commits[i]
        if 'Uncategorized' in str(c):
            feature_item = get_commit_data_cache().get(c.commit_hash)
            features = features_to_dict(feature_item)
            category, topic = CommitList.categorize(features)
            current_commits[i] = dataclasses.replace(
                c, category=category, topic=topic
            )
    current_commits.write_result()


def get_hash_or_pr_url(commit: Commit):
    # cdc = get_commit_data_cache()
    pr_link = commit.pr_link
    if pr_link is None:
        return commit.commit_hash
    else:
        regex = r'https://github.com/PaddlePaddle/Paddle/pull/([0-9]+)'
        matches = re.findall(regex, pr_link)
        if len(matches) == 0:
            return commit.commit_hash

        return f'[#{matches[0]}]({pr_link})'


def to_markdown(commit_list: CommitList, category):
    def cleanup_title(commit):
        match = re.match(r'(.*) \(#\d+\)', commit.title)
        if match is None:
            return commit.title
        return match.group(1)

    merge_mapping = defaultdict(list)
    for commit in commit_list.commits:
        if commit.merge_into:
            merge_mapping[commit.merge_into].append(commit)

    cdc = get_commit_data_cache()
    lines = [f'\n## {category}\n']
    for topic in topics:
        lines.append(f'### {topic}\n')
        commits = commit_list.filter(category=category, topic=topic)
        if '_' in topic:
            commits.extend(
                commit_list.filter(
                    category=category, topic=topic.replace('_', ' ')
                )
            )
        if ' ' in topic:
            commits.extend(
                commit_list.filter(
                    category=category, topic=topic.replace(' ', '_')
                )
            )
        for commit in commits:
            if commit.merge_into:
                continue
            all_related_commits = merge_mapping[commit.commit_hash] + [commit]
            commit_list_md = ", ".join(
                get_hash_or_pr_url(c) for c in all_related_commits
            )
            result = f'- {cleanup_title(commit)} ({commit_list_md})\n'
            lines.append(result)
    return lines


def get_markdown_header(category):
    header = f"""
# Release Notes worksheet {category}
- polish PR title to make it human read friendly.
- edit, delete, merge multiple PRs.
- summarize notes for this category.
"""

    return [
        header,
    ]


def main():
    parser = argparse.ArgumentParser(description='Tool to create a commit list')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--create_new', nargs=2)
    group.add_argument('--update_to')
    # I found this flag useful when experimenting with adding new auto-categorizing filters.
    # After running commitlist.py the first time, if you add any new filters in this file,
    # re-running with "rerun_with_new_filters" will update the existing commitlist.csv file,
    # but only affect the rows that were previously marked as "Uncategorized"
    group.add_argument('--rerun_with_new_filters', action='store_true')
    group.add_argument('--stat', action='store_true')
    group.add_argument('--export_markdown', action='store_true')
    group.add_argument('--export_csv_categories', action='store_true')
    parser.add_argument('--path', default='results/commitlist.csv')
    args = parser.parse_args()

    if args.create_new:
        create_new(args.path, args.create_new[0], args.create_new[1])
        return
    if args.update_to:
        update_existing(args.path, args.update_to)
        return
    if args.rerun_with_new_filters:
        rerun_with_new_filters(args.path)
        return
    if args.stat:
        commits = CommitList.from_existing(args.path)
        stats = commits.stat()
        pprint.pprint(stats)
        return

    if args.export_csv_categories:
        commits = CommitList.from_existing(args.path)
        categories = list(commits.stat().keys())
        for category in categories:
            print(f"Exporting {category}...")
            filename = f'results/export/result_{category}.csv'
            CommitList.write_to_disk_static(
                filename, commits.filter(category=category)
            )
        return

    if args.export_markdown:
        commits = CommitList.from_existing(args.path)
        categories = list(commits.stat().keys())
        for category in categories:
            print(f"Exporting {category}...")
            lines = get_markdown_header(category)
            lines += to_markdown(commits, category)
            filename = f'results/export/result_{category}.md'
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'w') as f:
                f.writelines(lines)
        return
    raise AssertionError()


if __name__ == '__main__':
    main()
