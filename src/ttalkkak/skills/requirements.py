from ttalkkak.schemas import Requirements
from ttalkkak.skills.loader import Skill


def merge_requirements(skills: list[Skill]) -> Requirements:
    req = Requirements()
    for s in skills:
        for flag, on in s.ext.requires.items():
            if on:
                req.flags[flag] = True
                req.sources.setdefault(flag, []).append(s.name)
        if s.ext.min_context > req.min_context:
            req.min_context = s.ext.min_context
            req.sources["min_context"] = [s.name]
        elif s.ext.min_context and s.ext.min_context == req.min_context:
            req.sources.setdefault("min_context", []).append(s.name)
    return req
