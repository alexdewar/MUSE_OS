"""Output cached quantities

Functions that output the state of diverse quantities at intermediate steps of the
calculation.

The core of the method is the OutputCache class that initiated by the MCA with input
parameters defined in the TOML file, much like the existing 'output' options but in a
'outputs_cache' list, enables a channel to "listen" for data to be cached and, after
each period, saved into disk via the 'consolidate_cache' method.

Anywhere in the code, you can write:

.. code-block:: python

    pub.sendMessage("cache_quantity", quantity=quantity_name, data=some_data)

If the quantity has been set as something to cache, the data will be stored and,
eventually, save to disk after - possibly - agregating the data and remove those entries
corresponding to non-convergent investment attempts. This process of cleaning and
aggregation is quantity specific.
"""
from __future__ import annotations

import inspect
from collections import ChainMap
from functools import reduce
from typing import List, Mapping, Text, Union, Callable, Optional, MutableMapping
from operator import attrgetter


from pubsub import pub
import xarray as xr
import pandas as pd

from muse.registration import registrator
from muse.sectors import AbstractSector


OUTPUT_QUANTITY_SIGNATURE = Callable[
    [List[xr.DataArray]], Union[xr.DataArray, pd.DataFrame]
]
"""Signature of functions computing quantities for later analysis."""

OUTPUT_QUANTITIES: MutableMapping[Text, OUTPUT_QUANTITY_SIGNATURE] = {}
"""Quantity for post-simulation analysis."""


@registrator(registry=OUTPUT_QUANTITIES)
def register_output_quantity(function: OUTPUT_QUANTITY_SIGNATURE) -> Callable:
    """Registers a function to compute an output quantity."""
    from functools import wraps

    @wraps(function)
    def decorated(*args, **kwargs):
        result = function(*args, **kwargs)
        if isinstance(result, (pd.DataFrame, xr.DataArray)):
            result.name = function.__name__
        return result

    return decorated


class OutputCache:
    """Creates outputs functions for post-mortem analysis of cached quantities.

    Each parameter is a dictionary containing the following:

    - quantity (mandatory): name of the quantity to output. Mandatory.
    - sink (optional): name of the storage procedure, e.g. the file format
      or database format. When it cannot be guessed from `filename`, it defaults to
      "csv".
    - filename (optional): path to a directory or a file where to store the quantity. In
      the latter case, if sink is not given, it will be determined from the file
      extension. The filename can incorporate markers. By default, it is
      "{default_output_dir}/{sector}{year}{quantity}{suffix}".
    - any other parameter relevant to the sink, e.g. `pandas.to_csv` keyword
      arguments.

    For simplicity, it is also possible to given lone strings as input.
    They default to `{'quantity': string}` (and the sink will default to
    "csv").
    """

    def __init__(
        self,
        *parameters: Mapping,
        output_quantities: Optional[
            MutableMapping[Text, OUTPUT_QUANTITY_SIGNATURE]
        ] = None,
        sectors: Optional[List[AbstractSector]] = None,
        topic: str = "cache_quantity"
    ):
        from muse.outputs.sector import _factory

        output_quantities = (
            OUTPUT_QUANTITIES if output_quantities is None else output_quantities
        )
        self.agents: MutableMapping[Text, MutableMapping[Text, Text]] = (
            extract_agents(sectors) if sectors is not None else {}
        )

        self.to_save: Mapping[str, xr.Dataset] = {
            p["quantity"]: xr.Dataset()
            for p in parameters
            if p["quantity"] in output_quantities
        }
        self.factory: Mapping[str, Callable] = {
            p["quantity"]: _factory(output_quantities, p, sector_name="Cache")
            for p in parameters
            if p["quantity"] in self.to_save
        }
        pub.subscribe(self.cache, topic)

    def cache(self, data: xr.DataArray, quantity: Optional[Text] = None) -> None:
        """Caches the data into memory for the given quantity.

        Args:
            data (xr.DataArray): The data to be cache.
            quantity (Optional[Text]): The quantity this data relates to.
        """
        quantity = quantity if quantity is not None else data.name

        if quantity not in self.to_save:
            return

        order = len(self.to_save[quantity])
        self.to_save[quantity][order] = data.copy().rename(quantity)

    def consolidate_cache(self) -> None:
        """Save the cached data into disk and flushes cache.

        This method is meant to be called after each time period in the main loop of the
        MCA, just after market quantities are saved.

        Args:
            year (int): Year being simulated.
        """
        for quantity, cache in self.to_save.items():
            if len(cache) == 0:
                continue
            self.factory[quantity](cache, self.agents)
        self.to_save = {q: xr.Dataset() for q in self.to_save}


def extract_agents(
    sectors: List[AbstractSector],
) -> MutableMapping[Text, MutableMapping[Text, Text]]:
    """_summary_

    Args:
        sectors (List[AbstractSector]): _description_

    Returns:
        Mapping[Text, Text]: _description_
    """
    return ChainMap(*[extract_agents_internal(sector) for sector in sectors])


def extract_agents_internal(
    sector: AbstractSector,
) -> MutableMapping[Text, MutableMapping[Text, Text]]:
    """_summary_

    Args:
        sector (AbstractSector): _description_

    Returns:
        Mapping[Text, Text]: _description_
    """
    info: MutableMapping[Text, MutableMapping[Text, Text]] = {}
    sector_name = getattr(sector, "name", "unnamed")
    agents = sorted(getattr(sector, "agents", []), key=attrgetter("name"))
    for agent in agents:
        aid = agent.uuid
        info[aid] = {}
        info[aid]["agent"] = agent.name
        info[aid]["type"] = agent.category
        info[aid]["sector"] = sector_name

    return info


def combine_arrays(data: xr.Dataset, quantity: str) -> pd.DataFrame:
    """_summary_

    Args:
        data (_type_, optional): _description_. Defaults to List[xr.DataArray].

    Returns:
        pd.DataFrame: _description_
    """
    return reduce(
        lambda left, right: pd.DataFrame.merge(left, right, how="right"),
        [data[order].rename(quantity).to_dataframe().reset_index() for order in data],
    )


@register_output_quantity
def capacity(
    cached: xr.Dataset, agents: MutableMapping[Text, MutableMapping[Text, Text]]
) -> pd.DataFrame:
    """Consolidates the cached capacities into a single DataFrame to save."""
    frame = inspect.currentframe()
    quantity = inspect.getframeinfo(frame).function
    data = combine_arrays(cached, quantity)
    data = data[data[quantity] != 0]

    for agent in list(agents):
        filter = data.agent == agent
        for key, value in agents[agent].items():
            data.loc[filter, key] = value

    return data[sorted(data.columns)]


@register_output_quantity
def production(cached: List[xr.DataArray]) -> pd.DataFrame:
    """Consolidates the cached production into a single DataFrame to save."""
    pass


@register_output_quantity
def lcoe(cached: List[xr.DataArray]) -> pd.DataFrame:
    """Consolidates the cached LCOE into a single DataFrame to save."""
    pass
