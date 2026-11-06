"""The web layer: one router per area, the forms behind them and the templates they render.

A page is a thin thing here. It reads and writes through `app.services`, so a URL can only do what
a service already allows, and the rules for what a channel or a setting may be exist in one place
for the browser and for the console alike.
"""
