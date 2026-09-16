# files.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os


def get_content(file_name):
    with open(file_name, "r") as f1:
        file_content = f1.read()
    return file_content


def create(file_name):
    dir_name = os.path.dirname(file_name)

    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name)

    open(file_name, "w+").close()


def create_path(path):
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except:
            raise


def insert_file(base_file, add_file, new_file, keyword):
    src_file = open(base_file, "r")
    file_add = open(add_file, "r")

    content = src_file.read()
    src_file.close()

    content_add = file_add.read()
    file_add.close()
    pos = content.find(keyword)
    if pos != -1:
        content = content[:pos] + content_add + content[pos:]
        new_file = open(new_file, "w")
        new_file.write(content)
        new_file.close()


def insert_str(base_file, add_str, new_file, keyword):
    src_file = open(base_file, "r")
    content = src_file.read()
    src_file.close()

    pos = content.find(keyword)
    if pos != -1:
        content = content[:pos] + add_str + content[pos:]
        new_file = open(new_file, "w")
        new_file.write(content)
        new_file.close()


def replace(base_file, new_file, pattern, value):
    fd = open(base_file, "r")
    content = fd.read()
    fd.close()

    content = content.replace(pattern, value)

    fd = open(new_file, "w")
    fd.write(content)
    fd.close()


def write_content(filename, content):
    fd = open(filename, "w+")
    fd.write(content + "\n")
    fd.close()


def merge_content(original_filename1, original_filename2, new_filename):
    new_file = open(new_filename, "a+")
    with open(original_filename1, "r") as f1:
        file1_content = f1.read()
        new_file.write(file1_content)
    with open(original_filename2, "r") as f2:
        file2_content = f2.read()
        new_file.write(file2_content)


def append_content(filename, content):
    fd = open(filename, "a+")
    fd.write(content + "\n")
    fd.close()


def remove_file(file):
    if os.path.exists(file):
        os.remove(file)
    else:
        pass
