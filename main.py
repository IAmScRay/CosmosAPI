import bech32
import time
from datetime import datetime, timezone, timedelta
from typing import List

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from config import *


class CommissionModel(BaseModel):
    rate: int = Field(
        ...,
        description="Current commission rate percentage"
    )
    max_rate: int = Field(
        ...,
        description="Maximum rate this validator can set"
    )
    max_change_rate: int = Field(
        ...,
        description="Maximum rate of daily commission rate change"
    )
    last_update: str = Field(
        ...,
        description="Date of last commission update"
    )


class ValidatorModel(BaseModel):
    moniker: str = Field(
        ...,
        description="Validator's name"
    )
    description: str = Field(
        ...,
        description="Validator's description"
    )
    website: str = Field(
        ...,
        description="Validator's website"
    )
    identity: str = Field(
        ...,
        description="Validator's keybase identity ID"
    )
    valoper_address: str = Field(
        ...,
        description="Validator's operating address"
    )
    address: str = Field(
        ...,
        description="Validator's owner address"
    )
    tokens: int = Field(
        ...,
        description="Validator's stake"
    )
    commission_data: CommissionModel


class ValidatorListModel(BaseModel):
    bonded_validators: int = Field(
        ...,
        description="Amount of current bonded validators"
    )
    max_validators: int = Field(
        ...,
        description="Maximum amount of network validators"
    )
    validators: List[ValidatorModel] = Field(
        ...,
        description="List of current network validators"
    )


class BlockHeightModel(BaseModel):
    height: int = Field(
        ...,
        description="Current block height"
    )


class DelegatorsModel(BaseModel):
    delegators: int = Field(
        ...,
        description="Delegators amount"
    )


class AccountModel(BaseModel):
    balance: float = Field(
        ...,
        description="Token balance"
    )
    total_delegated: float = Field(
        ...,
        description="Total amount of tokens delegated to validators"
    )
    delegated_to: int = Field(
        ...,
        description="Numbers of validators this address delegated tokens to"
    )
    rewards: float = Field(
        ...,
        description="Total accrued rewards from all validators"
    )


errors = 0

if CHAIN_API_SERVER == "" or (
        not CHAIN_API_SERVER.startswith("http://") and
        not CHAIN_API_SERVER.startswith("https://")
):
    print("Missing URL for an chain's API server!")
    errors += 1

if COIN_DENOM <= 0:
    print("Incorrect coin denomination!")
    errors += 1

if HRP_PREFIX == "":
    print("Missing HRP prefix for wallet addresses!")
    errors += 1

if errors > 0:
    print("Make sure you configured `config.py` properly & try again.")
    exit(-1)

app = FastAPI()

last_fetch = 0
current_set = []
max_validators = 0


def get_address_from_valoper(valoper_address: str) -> str:
    decoded = bech32.bech32_decode(valoper_address)
    return bech32.bech32_encode(HRP_PREFIX, decoded[1])


def format_date(date: str) -> str:
    date = date.split(".")[0]
    if "Z" in date:
        date_format = "%Y-%m-%dT%H:%M:%SZ"
    else:
        date_format = "%Y-%m-%dT%H:%M:%S"

    date_obj = datetime.strptime(date, date_format)
    date_obj = date_obj.replace(tzinfo=timezone.utc)
    utc_3 = timezone(timedelta(hours=3))

    return date_obj.astimezone(utc_3).strftime("%d.%m.%Y %H:%M:%S")


def reformat_data(val_data: list) -> list:
    result = []

    for validator in val_data:
        data = {
            "moniker": validator["description"]["moniker"],
            "description": validator["description"]["details"],
            "website": validator["description"]["website"],
            "identity": validator["description"]["identity"],
            "valoper_address": validator["operator_address"],
            "address": get_address_from_valoper(validator["operator_address"]),
            "tokens": int(validator["tokens"]) // 10 ** COIN_DENOM,
            "commission_data": {
                "rate": int(
                    float(validator["commission"]["commission_rates"]["rate"]) * 100
                ),
                "max_rate": int(
                    float(validator["commission"]["commission_rates"]["max_rate"]) * 100
                ),
                "max_change_rate": int(
                    float(validator["commission"]["commission_rates"]["max_change_rate"]) * 100
                ),
                "last_update": format_date(validator["commission"]["update_time"])
            }
        }
        result.append(data)

    result = sorted(result, key=lambda val: val["tokens"], reverse=True)
    return result


@app.get(
    path="/block_height",
    summary="Get current block height.",
    response_model=BlockHeightModel
)
async def block_height() -> dict:
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/json"
        },
        timeout=60
    ) as client:
        req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/base/tendermint/v1beta1/validatorsets/latest"
        )

        resp = req.json()

    return {
        "height": int(resp["block_height"])
    }


@app.get(
    path="/validators_info",
    summary="Get current validator set",
    response_description="List of validators sorted by stake (descending) & some network parameters",
    response_model=ValidatorListModel
)
async def validators_info():
    global current_set, last_fetch, max_validators

    current_time = int(time.time())
    diff = current_time - last_fetch
    if diff >= 60:
        set_data = await get_val_set_data()
        current_set = reformat_data(set_data)
        max_validators = await get_max_validators()

        last_fetch = int(time.time())

    return {
        "bonded_validators": len(current_set),
        "max_validators": max_validators,
        "validators": current_set
    }


@app.get(
    path="/delegators",
    summary="Get the delegators amount of a certain validator",
    response_model=DelegatorsModel
)
async def get_delegators(valoper_address: str):
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/json"
        },
        timeout=60
    ) as client:
        req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/staking/v1beta1/validators/{valoper_address}/delegations"
        )

        resp = req.json()

    return {
        "delegators": len(resp["delegation_responses"])
    }


@app.get(
    path="/address/{address}",
    summary="Get information about an address",
    response_model=AccountModel
)
async def get_address(address: str):
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/json"
        },
        timeout=60
    ) as client:
        balance_req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/bank/v1beta1/balances/{address}"
        )

        delegation_req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/staking/v1beta1/delegations/{address}"
        )

        rewards_req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/distribution/v1beta1/delegators/{address}/rewards"
        )

        balance_resp = balance_req.json()
        delegation_req = delegation_req.json()
        rewards_req = rewards_req.json()

    if "code" in balance_resp or "code" in delegation_req or "code" in rewards_req:
        return {
            "balance": -1,
            "total_delegated": 0.0,
            "delegated_to": 0,
            "rewards": 0.0
        }

    total = 0.0
    for delegation in delegation_req["delegation_responses"]:
        total += round(
            int(delegation["balance"]["amount"]) / 10 ** COIN_DENOM,
            ndigits=COIN_DENOM
        )

    rewards = 0.0
    if len(rewards_req["total"]) > 0:
        rewards = int(float(rewards_req["total"][0]["amount"])) / 10 ** COIN_DENOM

    return {
        "balance": int(balance_resp["balances"][0]["amount"]) / 10 ** COIN_DENOM,
        "total_delegated": total,
        "delegated_to": len(delegation_req["delegation_responses"]),
        "rewards": rewards
    }


async def get_val_set_data() -> list:
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/json"
        },
        timeout=60
    ) as client:
        req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED"
        )

        resp = req.json()

    return resp["validators"]


async def get_max_validators() -> int:
    async with httpx.AsyncClient(
        headers={
            "Accept": "application/json"
        },
        timeout=60
    ) as client:
        req = await client.get(
            url=f"{CHAIN_API_SERVER}/cosmos/staking/v1beta1/params"
        )

        resp = req.json()

    return resp["params"]["max_validators"]
