"""Interval estimates, with no SciPy dependency (copied from owizdom/sticky-fingers).

Zero wrong blames in 23 cases does not mean the rate is zero; it bounds it. Every rate in the
report carries an interval so a small corpus cannot be read as a strong claim.

  wilson()            two-sided score interval; well behaved at p = 0 and p = 1
  zero_event_upper()  the exact one-sided bound when k = 0 ("rule of three")
"""

import math
from dataclasses import dataclass

# z_{1-alpha/2} for the alphas we use; avoids pulling in SciPy for one lookup.
_Z = {0.10: 1.6448536269514722, 0.05: 1.959963984540054, 0.01: 2.5758293035489004}


def _z(alpha: float) -> float:
    if alpha in _Z:
        return _Z[alpha]
    # Acklam's inverse-normal approximation, |error| < 1.15e-9
    p = 1.0 - alpha / 2.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - pl:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


@dataclass
class Interval:
    point: float
    lo: float
    hi: float
    n: int
    k: int

    def __str__(self) -> str:
        return f"{self.point:.3f} [{self.lo:.3f}, {self.hi:.3f}] (n={self.n})"


def wilson(k: int, n: int, alpha: float = 0.05) -> Interval:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return Interval(float("nan"), float("nan"), float("nan"), 0, 0)
    z = _z(alpha)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Interval(p, max(0.0, center - half), min(1.0, center + half), n, k)


def zero_event_upper(n: int, alpha: float = 0.05) -> float:
    """Exact one-sided upper bound on a rate after observing 0 events in n trials.

    1 - alpha**(1/n); the familiar 3/n 'rule of three' is its large-n limit.
    """
    if n <= 0:
        return 1.0
    return 1.0 - alpha ** (1.0 / n)
