#!/bin/bash

find $@ -iname *.h -o -iname *.cpp | xargs -P 8 $TB_CLANG_FORMAT -i
