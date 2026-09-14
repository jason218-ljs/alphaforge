"""归因分析：Brinson 行业归因 + Fama-French 多因子回归归因。

- Brinson：配置效应 + 选股效应 + 交互效应（行业维度）
- Fama-French：市场/规模(SMB)/价值(HML) 三因子回归（中国版风格），
  分解为 系统性收益 + 选股回报 + 因子暴露收益
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from alphaforge.logger import get_logger

logger = get_logger("analysis.attribution")


class AttributionAnalyzer:
    """归因分析器。"""

    # ---------- Brinson 行业归因 ----------

    def brinson_attribution(
        self,
        portfolio_weights: Dict[str, Dict[str, float]],   # {industry: weight}
        benchmark_weights: Dict[str, float],              # {industry: weight}
        portfolio_returns: Dict[str, float],              # {industry: return}
        benchmark_returns: Dict[str, float],              # {industry: return}
    ) -> Dict[str, Any]:
        """Brinson-Fachler 行业归因。

        Args:
            portfolio_weights: 组合行业权重（含总体 {_} 键表示总权重）。
            benchmark_weights: 基准行业权重。
            portfolio_returns: 组合各行业收益。
            benchmark_returns: 基准各行业收益。

        Returns:
            {allocation_effect, selection_effect, interaction_effect,
             total_effect, industry_detail}
        """
        industries = set(portfolio_weights) | set(benchmark_weights)
        allocation = 0.0
        selection = 0.0
        interaction = 0.0
        detail = []

        for ind in sorted(industries):
            wp = portfolio_weights.get(ind, 0.0)
            wb = benchmark_weights.get(ind, 0.0)
            rp = portfolio_returns.get(ind, 0.0)
            rb = benchmark_returns.get(ind, 0.0)

            alloc_eff = (wp - wb) * rb          # 配置效应
            sel_eff = wb * (rp - rb)             # 选股效应
            inter_eff = (wp - wb) * (rp - rb)    # 交互效应
            allocation += alloc_eff
            selection += sel_eff
            interaction += inter_eff
            detail.append({
                "industry": ind,
                "portfolio_weight": wp,
                "benchmark_weight": wb,
                "portfolio_return": rp,
                "benchmark_return": rb,
                "allocation_effect": alloc_eff,
                "selection_effect": sel_eff,
                "interaction_effect": inter_eff,
            })

        total = allocation + selection + interaction
        return {
            "allocation_effect": allocation,
            "selection_effect": selection,
            "interaction_effect": interaction,
            "total_effect": total,
            "excess_return": total,
            "industry_detail": detail,
        }

    # ---------- Fama-French 多因子归因 ----------

    def fama_french_attribution(
        self,
        portfolio_returns: List[float],     # 组合日收益序列
        market_returns: List[float],        # 市场日收益序列（基准）
        smb_returns: Optional[List[float]] = None,   # 规模因子（小-大）
        hml_returns: Optional[List[float]] = None,   # 价值因子（高账面市值比-低）
        risk_free_returns: Optional[List[float]] = None,  # 日无风险收益
        annualization: int = 252,
    ) -> Dict[str, Any]:
        """Fama-French 三因子回归归因（中国版近似）。

        回归模型：Rp - Rf = α + βm·(Rm - Rf) + βsmb·SMB + βhml·HML + ε

        Returns:
            {regression: {alpha, beta_market, beta_smb, beta_hml, r_squared},
             factor_contribution: {market, smb, hml, selection, total},
             decomposition: {systematic, selection, factor_exposure, alpha}}
        """
        n = len(portfolio_returns)
        if n < 3:
            return self._empty_fama()

        rf = risk_free_returns or [0.0] * n
        # 对齐长度
        m = min(n, len(market_returns), len(rf))
        if m < 3:
            return self._empty_fama()

        rp = np.array(portfolio_returns[:m], dtype="float64")
        rm = np.array(market_returns[:m], dtype="float64")
        rf_arr = np.array(rf[:m], dtype="float64")

        excess_p = rp - rf_arr
        excess_m = rm - rf_arr

        # 构建因子矩阵
        factor_names = ["market"]
        factor_cols = [excess_m]
        if smb_returns is not None and len(smb_returns) >= m:
            smb = np.array(smb_returns[:m], dtype="float64")
            factor_cols.append(smb)
            factor_names.append("smb")
        if hml_returns is not None and len(hml_returns) >= m:
            hml = np.array(hml_returns[:m], dtype="float64")
            factor_cols.append(hml)
            factor_names.append("hml")

        X = np.column_stack([np.ones(m)] + factor_cols)
        try:
            beta, *_ , resid_ss = np.linalg.lstsq(X, excess_p, rcond=None)
        except np.linalg.LinAlgError:
            return self._empty_fama()

        alpha = float(beta[0])
        betas = [float(b) for b in beta[1:]]
        factor_beta = dict(zip(factor_names, betas))

        # R²
        pred = X @ beta
        ss_tot = float(np.sum((excess_p - excess_p.mean()) ** 2))
        ss_res = float(np.sum((excess_p - pred) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        # 因子贡献（年化）
        contribution = {}
        for name, b in factor_beta.items():
            factor_returns = factor_cols[factor_names.index(name)]
            contribution[name] = float(b) * float(np.mean(factor_returns) * annualization)
        # 选股成分 = α × 年化
        selection_contrib = alpha * annualization

        systematic = sum(v for k, v in contribution.items())
        factor_exposure = sum(v for k, v in contribution.items())

        return {
            "regression": {
                "alpha": alpha,
                "beta_market": factor_beta.get("market", 0.0),
                "beta_smb": factor_beta.get("smb", 0.0),
                "beta_hml": factor_beta.get("hml", 0.0),
                "r_squared": r_squared,
                "factor_names": factor_names,
                "n_observations": m,
            },
            "factor_contribution": contribution,
            "selection_contribution": selection_contrib,
            "decomposition": {
                "systematic": systematic,          # 系统性（因子暴露）收益
                "factor_exposure": factor_exposure,
                "selection": selection_contrib,     # 选股（α）收益
                "total": systematic + selection_contrib,
            },
        }

    def _empty_fama(self) -> Dict[str, Any]:
        return {
            "regression": {
                "alpha": 0.0, "beta_market": 0.0, "beta_smb": 0.0, "beta_hml": 0.0,
                "r_squared": 0.0, "factor_names": [], "n_observations": 0,
            },
            "factor_contribution": {},
            "selection_contribution": 0.0,
            "decomposition": {"systematic": 0.0, "factor_exposure": 0.0,
                              "selection": 0.0, "total": 0.0},
        }

    # ---------- Fama 单因子分解（替代，当无 SMB/HML 时） ----------

    def fama_simple_decomposition(
        self, portfolio_returns: List[float], market_returns: List[float],
        risk_free_returns: Optional[List[float]] = None,
        annualization: int = 252,
    ) -> Dict[str, Any]:
        """简化 Fama 分解：系统性收益 + 选股回报（回归残差）。"""
        return self.fama_french_attribution(
            portfolio_returns, market_returns,
            smb_returns=None, hml_returns=None,
            risk_free_returns=risk_free_returns, annualization=annualization,
        )