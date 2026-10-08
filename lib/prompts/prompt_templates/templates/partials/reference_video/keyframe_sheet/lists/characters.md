{% for character in characters %}
{% if character.face %}
- {{ character.name }}: face from Image {{ character.face }}, build and outfit from Image {{ character.body }}.{% if character.appearance %} Appearance: {{ character.appearance }}{% endif %}

{% else %}
- {{ character.name }}: face, build and outfit from Image {{ character.body }}.{% if character.appearance %} Appearance: {{ character.appearance }}{% endif %}

{% endif %}
{% endfor %}