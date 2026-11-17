"""ЕГРЮЛ lookup worker: finds out who runs each company, so a cold call can
ask for a person instead of "директора".

The problem it solves: an OSM lead arrives with a switchboard number and
nothing else. A 495 number reaches a dispatcher, and "позовите директора"
is where the call ends. The state register publishes the name and post of
whoever runs every legal entity, and asking for someone by name gets past
that first gate far more often.

Source is DaData's suggest/party API, which mirrors ФНС's ЕГРЮЛ/ЕГРИП with
a lag of no more than three days. The free tier allows 10 000 requests per
day across all suggestion types - more than enough for a base this size.

Scope is deliberately narrow: name, post, ИНН, ОГРН, legal name. These are
facts the register publishes about a company's management. No personal
phones, no addresses, nothing beyond what ФНС itself puts on
egrul.nalog.ru - and nothing this worker could fetch even if asked.
"""
