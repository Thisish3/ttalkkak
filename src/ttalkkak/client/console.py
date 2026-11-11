import typer

from ttalkkak.client.config import load_config


def main() -> int:
    if load_config() is None:
        typer.echo("아직 접속 설정이 없습니다: ttalkkak login", err=True)
        return 2
    typer.echo("콘솔 대화는 아직 준비 중입니다", err=True)
    return 0
