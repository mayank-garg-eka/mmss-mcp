"""
HRP Risk Classifier — exact MMSS Yojna criteria (CEO slide 5, April 2026).

Input: dict of clinical values (all optional, None = unknown/not asked yet).
Output: {"level": "RED"|"YELLOW"|"GREEN", "reasons": [...]}
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClinicalInput:
    age: Optional[int] = None
    hb: Optional[float] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    parity: Optional[int] = None          # number of previous deliveries
    gestational_weeks: Optional[int] = None

    # Conditions (True = confirmed present)
    gdm: Optional[bool] = None            # gestational diabetes
    hiv: Optional[bool] = None
    tb_active: Optional[bool] = None
    hypothyroid: Optional[bool] = None
    heart_disease: Optional[bool] = None
    epilepsy: Optional[bool] = None
    bad_obs_history: Optional[bool] = None  # BOH
    prev_lscs: Optional[bool] = None
    ectopic_history: Optional[bool] = None
    aph: Optional[bool] = None            # placenta previa / abruptio
    haemoglobinopathy: Optional[bool] = None
    autoimmune: Optional[bool] = None     # SLE
    renal_disease: Optional[bool] = None
    jaundice_hepatitis: Optional[bool] = None
    pyrexia_infection: Optional[bool] = None  # malaria, dengue, H1N1, pyrexia
    abnormal_presentation: Optional[bool] = None  # >37 weeks
    iud_present: Optional[bool] = None
    multiple_pregnancy: Optional[bool] = None
    iugr: Optional[bool] = None
    hydramnios: Optional[bool] = None
    hyperthyroid: Optional[bool] = None
    differently_abled: Optional[bool] = None
    prolonged_infertility: Optional[bool] = None
    ivf: Optional[bool] = None
    post_dated: Optional[bool] = None     # >42 weeks
    vesicular_mole: Optional[bool] = None
    rh_isoimmunization: Optional[bool] = None
    cpd: Optional[bool] = None            # cephalopelvic disproportion


@dataclass
class HRPResult:
    level: str          # RED | YELLOW | GREEN
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "reasons": self.reasons}


def classify(data: ClinicalInput) -> HRPResult:
    red_reasons: list[str] = []
    yellow_reasons: list[str] = []
    green_reasons: list[str] = []

    # ── RED flags ────────────────────────────────────────────────────────────
    if data.bp_systolic is not None and data.bp_systolic >= 140:
        red_reasons.append("PIH / Preeclampsia (BP ≥ 140)")
    if data.hb is not None and data.hb < 7:
        red_reasons.append(f"Severe Anaemia (Hb {data.hb} g/dL < 7)")
    if data.age is not None and data.age < 16:
        red_reasons.append(f"Very young age ({data.age} years)")
    if data.age is not None and data.age > 40:
        red_reasons.append(f"Advanced maternal age ({data.age} years)")
    if data.weight_kg is not None and data.weight_kg < 40:
        red_reasons.append(f"Underweight ({data.weight_kg} kg < 40)")
    if data.weight_kg is not None and data.weight_kg > 75:
        red_reasons.append(f"Overweight ({data.weight_kg} kg > 75)")
    if data.gdm:
        red_reasons.append("Gestational Diabetes (GDM/DM)")
    if data.hiv:
        red_reasons.append("HIV positive")
    if data.tb_active:
        red_reasons.append("Active TB in pregnancy")
    if data.hypothyroid:
        red_reasons.append("Hypothyroidism")
    if data.heart_disease:
        red_reasons.append("Heart disease complicating pregnancy")
    if data.epilepsy:
        red_reasons.append("Epilepsy")
    if data.bad_obs_history:
        red_reasons.append("Bad Obstetric History (BOH)")
    if data.haemoglobinopathy:
        red_reasons.append("Haemoglobinopathy")
    if data.autoimmune:
        red_reasons.append("Auto-immune disease (SLE)")
    if data.renal_disease:
        red_reasons.append("Renal disease complicating pregnancy")
    if data.jaundice_hepatitis:
        red_reasons.append("Jaundice / Hepatitis B")
    if data.pyrexia_infection:
        red_reasons.append("Pyrexia / Malaria / Dengue / H1N1")
    if data.aph:
        red_reasons.append("APH — Placenta Previa / Abruptio Placenta")
    if data.ectopic_history:
        red_reasons.append("Ectopic pregnancy history")
    if data.abnormal_presentation and (data.gestational_weeks or 0) >= 37:
        red_reasons.append("Abnormal presentation at ≥37 weeks")
    if data.iud_present:
        red_reasons.append("IUD present in pregnancy")
    if data.multiple_pregnancy:
        red_reasons.append("Multiple pregnancy")

    if red_reasons:
        return HRPResult(level="RED", reasons=red_reasons)

    # ── YELLOW flags ─────────────────────────────────────────────────────────
    if data.age is not None and 16 <= data.age < 19:
        yellow_reasons.append(f"Teenage pregnancy ({data.age} years)")
    if data.hb is not None and 7.0 <= data.hb < 9.0:
        yellow_reasons.append(f"Moderate Anaemia (Hb {data.hb} g/dL)")
    if data.parity is not None and data.parity >= 4:
        yellow_reasons.append(f"Grand Multipara (parity {data.parity})")
    if data.prev_lscs:
        yellow_reasons.append("Previous LSCS / Assisted delivery")
    if data.height_cm is not None and data.height_cm < 145:
        yellow_reasons.append(f"Short Primi (height {data.height_cm} cm < 145)")
    if data.iugr:
        yellow_reasons.append("IUGR (Intra-Uterine Growth Restriction)")
    if data.hydramnios and (data.gestational_weeks or 0) >= 28:
        yellow_reasons.append("Hydramnios (poly/oligo) at ≥28 weeks")
    if data.hyperthyroid:
        yellow_reasons.append("Hyperthyroidism")
    if data.differently_abled:
        yellow_reasons.append("Differently abled mother")
    if data.prolonged_infertility:
        yellow_reasons.append("Pregnancy after prolonged infertility")
    if data.ivf:
        yellow_reasons.append("IVF pregnancy")
    if data.post_dated:
        yellow_reasons.append("Post-dated pregnancy (>42 weeks)")
    if data.vesicular_mole:
        yellow_reasons.append("Vesicular mole")

    if yellow_reasons:
        return HRPResult(level="YELLOW", reasons=yellow_reasons)

    # ── GREEN flags ───────────────────────────────────────────────────────────
    if data.hb is not None and data.hb >= 9.0:
        green_reasons.append(f"Mild Anaemia (Hb {data.hb} g/dL)")
    if data.rh_isoimmunization:
        green_reasons.append("Rh Isoimmunization")
    if data.cpd:
        green_reasons.append("CPD (Cephalopelvic Disproportion)")

    return HRPResult(level="GREEN", reasons=green_reasons)


def classify_from_dict(data: dict) -> dict:
    """Convenience wrapper: takes a plain dict, returns serialisable dict."""
    inp = ClinicalInput(**{k: v for k, v in data.items() if hasattr(ClinicalInput, k)})
    return classify(inp).to_dict()
