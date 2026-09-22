#!/bin/bash
# SANE 扫描后端激活 (hpaio + M1005 专用后端)
if [ -f /etc/sane.d/dll.conf ]; then
    grep -q '^hpaio' /etc/sane.d/dll.conf || echo 'hpaio' >> /etc/sane.d/dll.conf
    grep -q '^hpljm1005' /etc/sane.d/dll.conf || echo 'hpljm1005' >> /etc/sane.d/dll.conf
fi

# models.dat 自动找回补齐
if [ ! -f /usr/share/hplip/models.dat ]; then
    MODEL_PATH=$(find /usr -name "models.dat" 2>/dev/null | head -n 1)
    if [ -n "$MODEL_PATH" ]; then
        cp -f "$MODEL_PATH" /usr/share/hplip/ 2>/dev/null || true
        cp -f "$MODEL_PATH" /usr/share/hplip/data/models/ 2>/dev/null || true
    fi
fi
