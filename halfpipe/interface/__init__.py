# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from .conditions import ParseConditionFile
from .connectivity import ConnectivityMeasure
from .fixes import ApplyTransforms, FLAMEO, ReHo
from .fslnumpy import FLAME1, FilterRegressor, TemporalFilter
from .imagemaths import AddMeans, BlurInMask, MaskCoverage, MaxIntensity, Merge, MergeMask, Resample, ZScore
from .preprocessing import GrandMeanScaling
from .report import PlotEpi, PlotRegistration, Vals, CalcMean
from .resultdict import (
    MakeResultdicts,
    FilterResultdicts,
    AggregateResultdicts,
    ExtractFromResultdict,
    ResultdictDatasink,
)
from .stats import MakeDesignTsv, MakeDofVolume, InterceptOnlyModel, LinearModel
from .utility import (
    Exec,
    Filter,
    FilterList,
    Select,
    ToAFNI,
    FromAFNI,
    LoadResult,
    FillNA,
    MergeColumns,
    SelectColumns,
    Unvest
)

__all__ = [
    "ParseConditionFile",
    "ConnectivityMeasure",
    "ApplyTransforms",
    "FLAMEO",
    "ReHo",
    "FLAME1",
    "FilterRegressor",
    "TemporalFilter",
    "AddMeans",
    "BlurInMask",
    "MaskCoverage",
    "MaxIntensity",
    "Merge",
    "MergeMask",
    "Resample",
    "ZScore",
    "GrandMeanScaling",
    "PlotEpi",
    "PlotRegistration",
    "Vals",
    "CalcMean",
    "MakeResultdicts",
    "FilterResultdicts",
    "AggregateResultdicts",
    "ExtractFromResultdict",
    "ResultdictDatasink",
    "MakeDesignTsv",
    "MakeDofVolume",
    "InterceptOnlyModel",
    "LinearModel",
    "Exec",
    "Filter",
    "FilterList",
    "Select",
    "ToAFNI",
    "FromAFNI",
    "LoadResult",
    "FillNA",
    "MergeColumns",
    "SelectColumns",
    "Unvest",
]
