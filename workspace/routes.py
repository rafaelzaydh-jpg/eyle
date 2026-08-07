from flask import render_template


def register_routes(app):
    @app.route("/amor")
    def amor():
        return render_template('amor.html')
