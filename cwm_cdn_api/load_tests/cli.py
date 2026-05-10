import asyncclick as click


@click.group()
async def main():
    pass


@main.command()
async def cleanup():
    from .cleanup import main as cleanup_main

    cleanup_main()
