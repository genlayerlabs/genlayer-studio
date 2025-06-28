// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

library ArrayUtils {
	/**
	 * @dev Concatenates two arrays and drops an index from the first array
	 * @param _array1 The first array
	 * @param _array2 The second array
	 * @param _indexToDrop The index to drop from the first array
	 * @return result The concatenated array with the index dropped
	 */
	function concatArraysAndDropIndex(
		address[] memory _array1,
		address[] memory _array2,
		uint256 _indexToDrop
	) external pure returns (address[] memory result) {
		uint256 reduceLength = _array1.length > 0 &&
			_indexToDrop < _array1.length
			? 1
			: 0;
		result = new address[](_array1.length + _array2.length - reduceLength);
		uint256 resultIndex = 0;
		for (uint256 i = 0; i < _array1.length; i++) {
			if (i != _indexToDrop) {
				result[resultIndex] = _array1[i];
				resultIndex++;
			}
		}
		for (uint256 i = 0; i < _array2.length; i++) {
			result[resultIndex] = _array2[i];
			resultIndex++;
		}
	}

	/**
	 * @dev Gets the index of a key in an array
	 * @param _array The array to search
	 * @param _key The key to search for
	 * @return index The index of the key
	 * @return isFirst Whether the key is the first element in the array
	 */
	function getIndex(
		address[] memory _array,
		address _key
	) external pure returns (uint256 index, bool isFirst) {
		for (uint256 i = 0; i < _array.length; i++) {
			if (_array[i] == _key) {
				index = i;
				isFirst = i == 0;
				break;
			}
		}
	}
}