# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu
from nipype.algorithms import confounds as nac
from nipype.interfaces import fsl

from fmriprep import config

from ...interface import (
    MergeColumns,
    FillNA,
    MakeDofVolume,
    Resample,
    CalcMean,
    MaxIntensity,
    MakeResultdicts,
    ResultdictDatasink,
)
from ...utils import formatlikebids

from ..memory import MemoryCalculator


def _contrasts(map_timeseries_file=None, confounds_file=None):
    from pathlib import Path

    from halfpipe.io import loadspreadsheet
    import numpy as np
    import pandas as pd
    import csv

    map_timeseries_df = loadspreadsheet(map_timeseries_file)
    confounds_df = loadspreadsheet(confounds_file)
    m, n = map_timeseries_df.shape
    k, n = confounds_df.shape

    contrast_mat = np.zeros((m, m + k))
    contrast_mat[:m, :m] = 1

    leading_zeros = int(np.ceil(np.log10(m)))
    map_component_names = [f"{i:0{leading_zeros}d}" for i in range(1, m + 1)]

    contrast_columns = [*map_component_names, *confounds_df.columns]
    contrast_df = pd.DataFrame(contrast_mat, index=map_component_names, columns=contrast_columns)

    out_with_header = Path.cwd() / "merge_with_header.tsv"
    contrast_df.to_csv(
        out_with_header,
        sep="\t",
        index=True,
        na_rep="n/a",
        header=True,
        quoting=csv.QUOTE_NONNUMERIC,
    )
    out_no_header = Path.cwd() / "merge_no_header.tsv"
    contrast_df.to_csv(out_no_header, sep="\t", index=False, na_rep="n/a", header=False)
    return str(out_with_header), str(out_no_header), map_component_names


def init_dualregression_wf(
    workdir=None, feature=None, map_files=None, map_spaces=None, memcalc=MemoryCalculator()
):
    """
    create a workflow to calculate dual regression for ICA seeds
    """
    if feature is not None:
        name = f"{formatlikebids(feature.name)}_wf"
    else:
        name = "dualregression_wf"
    workflow = pe.Workflow(name=name)

    # input
    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=[
                "tags",
                "bold",
                "mask",
                "confounds_selected",
                "map_names",
                "map_files",
                "map_spaces",
            ]
        ),
        name="inputnode",
    )
    outputnode = pe.Node(niu.IdentityInterface(fields=["resultdicts"]), name="outputnode")

    if feature is not None:
        inputnode.inputs.map_names = feature.maps

    if map_files is not None:
        inputnode.inputs.map_files = map_files

    if map_spaces is not None:
        inputnode.inputs.map_spaces = map_spaces

    #
    statmaps = ["effect", "variance", "z", "dof", "mask"]
    make_resultdicts_a = pe.Node(
        MakeResultdicts(tagkeys=["feature", "map"], imagekeys=["design_matrix", "contrast_matrix"]),
        name="make_resultdicts_a",
    )
    if feature is not None:
        make_resultdicts_a.inputs.feature = feature.name
    workflow.connect(inputnode, "tags", make_resultdicts_a, "tags")
    workflow.connect(inputnode, "map_names", make_resultdicts_a, "map")
    make_resultdicts_b = pe.Node(
        MakeResultdicts(
            tagkeys=["feature", "map", "component"],
            imagekeys=statmaps,
            metadatakeys=["sources", "mean_t_s_n_r"],
        ),
        name="make_resultdicts_b",
    )
    if feature is not None:
        make_resultdicts_b.inputs.feature = feature.name
    workflow.connect(inputnode, "tags", make_resultdicts_b, "tags")
    workflow.connect(inputnode, "map_names", make_resultdicts_b, "map")

    workflow.connect(make_resultdicts_b, "resultdicts", outputnode, "resultdicts")

    #
    merge_resultdicts = pe.Node(niu.Merge(2), name="merge_resultdicts")
    workflow.connect(make_resultdicts_a, "resultdicts", merge_resultdicts, "in1")
    workflow.connect(make_resultdicts_b, "resultdicts", merge_resultdicts, "in2")
    resultdict_datasink = pe.Node(
        ResultdictDatasink(base_directory=workdir), name="resultdict_datasink"
    )
    workflow.connect(merge_resultdicts, "out", resultdict_datasink, "indicts")

    #
    resample = pe.MapNode(
        Resample(interpolation="LanczosWindowedSinc", reference_space="MNI152NLin2009cAsym"),
        name="resample",
        iterfield=["input_image", "input_space"],
        n_procs=config.nipype.omp_nthreads,
        mem_gb=memcalc.series_std_gb,
    )
    workflow.connect(inputnode, "map_files", resample, "input_image")
    workflow.connect(inputnode, "map_spaces", resample, "input_space")
    workflow.connect(inputnode, "bold", resample, "reference_image")

    # Delete zero voxels for the maps
    applymask = pe.MapNode(
        fsl.ApplyMask(), name="applymask", iterfield="in_file", mem_gb=memcalc.volume_std_gb,
    )
    workflow.connect(inputnode, "mask", applymask, "mask_file")
    workflow.connect(resample, "output_image", applymask, "in_file")

    # first step, calculate spatial regression of ICA components on to the
    # bold file
    spatialglm = pe.MapNode(
        fsl.GLM(out_file="beta", demean=True),
        name="spatialglm",
        iterfield="design",
        mem_gb=memcalc.series_std_gb * 10,
    )
    workflow.connect(applymask, "out_file", spatialglm, "design")
    workflow.connect(inputnode, "bold", spatialglm, "in_file")
    workflow.connect(inputnode, "mask", spatialglm, "mask")

    # second step, calculate the temporal regression of the time series
    # from the first step on to the bold file
    contrasts = pe.MapNode(
        niu.Function(
            input_names=["map_timeseries_file", "confounds_file"],
            output_names=["out_with_header", "out_no_header", "map_component_names"],
            function=_contrasts,
        ),
        iterfield="map_timeseries_file",
        name="contrasts",
    )
    workflow.connect(spatialglm, "out_file", contrasts, "map_timeseries_file")
    workflow.connect(inputnode, "confounds_selected", contrasts, "confounds_file")

    workflow.connect(contrasts, "out_with_header", make_resultdicts_a, "contrast_matrix")

    design = pe.MapNode(MergeColumns(2), iterfield=["in1", "column_names1"], name="design")
    workflow.connect(spatialglm, "out_file", design, "in1")
    workflow.connect(contrasts, "map_component_names", design, "column_names1")
    workflow.connect(inputnode, "confounds_selected", design, "in2")

    workflow.connect(design, "out_with_header", make_resultdicts_a, "design_matrix")

    fillna = pe.MapNode(FillNA(), iterfield="in_tsv", name="fillna")
    workflow.connect(design, "out_no_header", fillna, "in_tsv")

    temporalglm = pe.MapNode(
        fsl.GLM(
            out_cope="cope.nii.gz",
            out_varcb_name="varcope.nii.gz",
            out_z_name="zstat.nii.gz",
            demean=True,
        ),
        name="temporalglm",
        iterfield=["design", "contrasts"],
        mem_gb=memcalc.series_std_gb * 10,
    )
    workflow.connect(inputnode, "bold", temporalglm, "in_file")
    workflow.connect(inputnode, "mask", temporalglm, "mask")
    workflow.connect(fillna, "out_no_header", temporalglm, "design")
    workflow.connect(contrasts, "out_no_header", temporalglm, "contrasts")

    # make dof volume
    makedofvolume = pe.MapNode(MakeDofVolume(), iterfield=["design"], name="makedofvolume",)
    workflow.connect(inputnode, "bold", makedofvolume, "bold_file")
    workflow.connect(fillna, "out_no_header", makedofvolume, "design")

    for glmattr, resultattr in (("cope", "effect"), ("varcb", "variance", ("z", "z"))):
        split = pe.MapNode(
            fsl.Split(dimension="t"), iterfield="in_file", name=f"split{resultattr}images"
        )
        workflow.connect(temporalglm, f"out_{glmattr}", split, "in_file")
        workflow.connect(split, "out_files", make_resultdicts_b, resultattr)
    workflow.connect(makedofvolume, "out_file", make_resultdicts_b, "dof")
    workflow.connect(inputnode, "mask", make_resultdicts_b, "mask")

    #
    tsnr = pe.Node(nac.TSNR(), name="tsnr", mem_gb=memcalc.series_std_gb)
    workflow.connect(inputnode, "bold", tsnr, "in_file")

    maxintensity = pe.MapNode(
        MaxIntensity(), iterfield="in_file", name="maxintensity", mem_gb=memcalc.series_std_gb
    )
    workflow.connect(resample, "output_image", maxintensity, "in_file")

    calcmean = pe.MapNode(
        CalcMean(), iterfield="parcellation", name="calcmean", mem_gb=memcalc.series_std_gb
    )
    workflow.connect(maxintensity, "out_file", calcmean, "parcellation")
    workflow.connect(tsnr, "tsnr_file", calcmean, "in_file")

    workflow.connect(calcmean, "mean", make_resultdicts_b, "mean_t_s_n_r")

    #
    # mergesources = pe.MapNode(niu.Merge(3), iterfield="in3", name="mergesources")
    # workflow.connect(inputnode, "bold", mergesources, "in1")
    # workflow.connect(inputnode, "mask", mergesources, "in2")
    # workflow.connect(resample, "output_image", mergesources, "in3")
    #
    # workflow.connect(mergesources, "out", make_resultdicts, "sources")

    return workflow
