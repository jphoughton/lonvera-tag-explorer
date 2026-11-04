from app.projection.drivers import MODULE


def test_module_exports():
    assert MODULE.key == "drivers" and MODULE.compute and MODULE.load_inputs and MODULE.blueprint
