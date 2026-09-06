"""
Product freshness models.
"""

from pydantic import BaseModel, Field
from typing import Optional


class ProductFreshness(BaseModel):
    """Freshness verdict for one product: is its newest row recent enough?"""
    verdict: str = Field(..., description="VALID / INVALID / INDETERMINATE")
    age_s: Optional[float] = Field(None, description="Age of the newest row, in seconds")
    expected_s: int = Field(..., description="Expected max age, in seconds")
    channel: str = Field(..., description="Channel the product was read from")
