# passthrough_pykernel/passthrough_pykernel_placed.py -*- Python -*-
#
# This file is licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# (c) Copyright 2024 Advanced Micro Devices, Inc. or its affiliates
import numpy as np
import sys

from aie.dialects.aie import *
from aie.dialects.aiex import *
from aie.extras.context import mlir_mod_ctx
from aie.helpers.dialects.ext.func import func
from aie.helpers.dialects.ext.scf import _for as range_


dev = AIEDevice.npu1_1col

if len(sys.argv) > 2:
    if sys.argv[2] == "npu":
        dev = AIEDevice.npu1_1col
    elif sys.argv[2] == "npu2":
        dev = AIEDevice.npu2
    else:
        raise ValueError("[ERROR] Device name {} is unknown".format(sys.argv[2]))


def passthroughKernel(vector_size):
    N = vector_size
    lineWidthInBytes = N // 4  # chop input in 4 sub-tensors

    @device(dev)
    def device_body():
        # define types
        line_ty = np.ndarray[(lineWidthInBytes,), np.dtype[np.uint8]]

        # AIE Core Python Function declarations
        @func(emit=True)
        def passThroughLine(input: line_ty, output: line_ty, lineWidth: np.int32):
            for i in range_(lineWidth):
                output[i] = input[i]

        # Tile declarations
        ShimTile = tile(0, 0)
        ComputeTile2 = tile(0, 2)

        # AIE-array data movement with object fifos
        of_in = object_fifo("in", ShimTile, ComputeTile2, 2, line_ty)
        of_out = object_fifo("out", ComputeTile2, ShimTile, 2, line_ty)

        # Set up compute tiles

        # Compute tile 2
        @core(ComputeTile2)
        def core_body():
            for _ in range_(sys.maxsize):
                elemOut = of_out.acquire(ObjectFifoPort.Produce, 1)
                elemIn = of_in.acquire(ObjectFifoPort.Consume, 1)
                passThroughLine(elemIn, elemOut, lineWidthInBytes)
                of_in.release(ObjectFifoPort.Consume, 1)
                of_out.release(ObjectFifoPort.Produce, 1)

        #    print(ctx.module.operation.verify())

        vector_ty = np.ndarray[(N,), np.dtype[np.uint8]]

        @runtime_sequence(vector_ty, vector_ty, vector_ty)
        def sequence(inTensor, outTensor, notUsed):
            in_task = shim_dma_single_bd_task(
                of_in, inTensor, sizes=[1, 1, 1, N], issue_token=True
            )
            out_task = shim_dma_single_bd_task(
                of_out, outTensor, sizes=[1, 1, 1, N], issue_token=True
            )

            dma_start_task(in_task, out_task)
            dma_await_task(in_task, out_task)


try:
    vector_size = int(sys.argv[1])
    if vector_size % 64 != 0 or vector_size < 512:
        print("Vector size must be a multiple of 64 and greater than or equal to 512")
        raise ValueError
except ValueError:
    print("Argument has inappropriate value")
with mlir_mod_ctx() as ctx:
    passthroughKernel(vector_size)
    print(ctx.module)
