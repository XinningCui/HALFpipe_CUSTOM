# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from nipype.pipeline import engine as pe
from nipype.interfaces import utility as niu

from ...interfaces.utility.tsv import SelectColumns, FillNA
from ...interfaces.fslnumpy.regfilt import FilterRegressor
from ...utils.hash import b32_digest

from ..memory import MemoryCalculator


def init_confounds_select_wf(
        confound_names: list[str] | None = None,
        name: str | None = None,
        suffix: str | None = None
):
    if name is None:
        if confound_names is not None:
            name = f"confounds_select_{b32_digest(confound_names)[:4]}_wf"
        else:
            name = "confounds_select_wf"
    if suffix is not None:
        name = f"{name}_{suffix}"

    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(
            fields=["bold", "confounds", "confound_names", "mask", "vals"]
        ),
        name="inputnode",
    )
    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=["bold", "confounds_selected", "confounds", "mask", "vals"]
        ),
        name="outputnode",
    )
    workflow.connect(inputnode, "bold", outputnode, "bold")
    workflow.connect(inputnode, "confounds", outputnode, "confounds")
    workflow.connect(inputnode, "mask", outputnode, "mask")
    workflow.connect(inputnode, "vals", outputnode, "vals")

    if confound_names is not None:
        inputnode.inputs.confound_names = confound_names

    selectcolumns = pe.Node(SelectColumns(), name="selectcolumns")
    workflow.connect(inputnode, "confounds", selectcolumns, "in_file")
    workflow.connect(inputnode, "confound_names", selectcolumns, "column_names")

    workflow.connect(selectcolumns, "out_with_header", outputnode, "confounds_selected")

    return workflow


def init_confounds_regression_wf(
        name: str = "confounds_regression_wf",
        suffix: str | None = None,
        memcalc: MemoryCalculator = MemoryCalculator.default()
):
    if suffix is not None:
        name = f"{name}_{suffix}"
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(
        niu.IdentityInterface(fields=["bold", "confounds_selected", "confounds", "mask", "vals"]), name="inputnode",
    )
    outputnode = pe.Node(
        niu.IdentityInterface(
            fields=["bold", "confounds", "mask", "vals"]
        ),
        name="outputnode",
    )
    workflow.connect(inputnode, "mask", outputnode, "mask")
    workflow.connect(inputnode, "vals", outputnode, "vals")

    fillna = pe.Node(FillNA(), name="fillna")
    workflow.connect(inputnode, "confounds_selected", fillna, "in_tsv")

    filter_regressor_b = pe.Node(
        FilterRegressor(aggressive=True, filter_all=True, mask=False),
        name="filter_regressor_b",
        mem_gb=memcalc.series_std_gb
    )
    workflow.connect(inputnode, "bold", filter_regressor_b, "in_file")
    workflow.connect(inputnode, "mask", filter_regressor_b, "mask")
    workflow.connect(fillna, "out_no_header", filter_regressor_b, "design_file")

    workflow.connect(filter_regressor_b, "out_file", outputnode, "bold")

    filter_regressor_c = pe.Node(
        FilterRegressor(aggressive=True, filter_all=True, mask=False),
        name="filter_regressor_c",
        mem_gb=memcalc.min_gb
    )
    workflow.connect(inputnode, "confounds", filter_regressor_c, "in_file")
    workflow.connect(fillna, "out_no_header", filter_regressor_c, "design_file")

    workflow.connect(filter_regressor_c, "out_file", outputnode, "confounds")

    return workflow
