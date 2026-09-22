#!/bin/bash
set -e

echo ">>> [Drivers] 下载并部署 foo2zjs 固件微码..."
mkdir -p /usr/share/foo2zjs/firmware /usr/share/foo2xqx/firmware
cd /tmp
for m in 1005 1007 1008 1020; do 
    getweb $m 2>/dev/null || true
done
cp -f *.dl /usr/share/foo2zjs/firmware/ 2>/dev/null || true
cp -f *.dl /usr/share/foo2xqx/firmware/ 2>/dev/null || true
rm -rf /tmp/*
echo ">>> [Drivers] foo2zjs 固件准备就绪。"
