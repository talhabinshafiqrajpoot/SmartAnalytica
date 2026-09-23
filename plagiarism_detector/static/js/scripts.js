// static/js/scripts.js

// Custom JavaScript can go here
console.log("Custom JavaScript loaded");

// static/js/scripts.js
function onlyOneCheckbox(checkbox) {
  var checkboxes = document.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach((cb) => {
    if (cb !== checkbox) cb.checked = false;
  });
}
